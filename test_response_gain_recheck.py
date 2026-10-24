#!/usr/bin/env python3
"""Deterministic, hardware-free regressions for alignment-first gain diagnostics.

The first-correction scenarios use the owner's observed 2.078 Y gain; the
second-correction scenario reproduces their observed 2.372 X gain. These tests
prove control-flow safety, not camera accuracy or physical tool calibration.
"""
import tempfile
import test_stage6_offline as base


class ResponseSimulation(base.SimGate):
    def __init__(self, cfg, outcome='first_confirmed'):
        super().__init__(cfg)
        self.outcome=outcome
        self.labels=[]

    def measure(self, matrix, label):
        self.labels.append(label)
        second=self.outcome.startswith('second_')
        if label.startswith('settle_'):
            i=int(label.split('_')[-1])
            error=([.064105423213,.034026017908] if i==1 else [.061956411556,.022470011826]) if second else (
                [.0808,.0325] if i==1 else [.0854,.0394])
        elif second:
            if label=='cycle_01':
                error=[.048960973137,.025544855550]
            elif label=='cycle_02':
                error=[.031140395337,.035377597567]
            elif label=='cycle_03':
                error=[-.027957425241,-.015745903024]
                if self.outcome=='second_outside':error=[-.035,-.015745903024]
                if self.outcome=='second_gross':error=[-.082,-.015745903024]  # ~0.113 X displacement
            elif label.startswith('gain_recheck_'):
                error=([-.0271,-.0160] if label.endswith('_01') else [-.0273,-.0158])
                if self.outcome=='second_unstable' and label.endswith('_02'):
                    error=[-.0070,-.0158]  # >0.015 change from first recheck
            else:
                raise AssertionError('Unexpected second-write sequence '+label)
        elif label=='cycle_01':
            error=[.094800855386,.041927292168]
        elif label=='cycle_02':
            if self.outcome=='out_of_tolerance':
                error=[.035,.000374231489]
            elif self.outcome=='gross':
                error=[-.02,.000374231489]  # 0.115 mm X response; >.11 hard cap
            elif self.outcome=='reverse':
                error=[.016466185512,.050]
            elif self.outcome=='high_gain':
                error=[.016466185512,-.010172707832] # Y gain ~2.605 but bounded and in tolerance
            elif self.outcome=='normal_gain':
                error=[.0198,.021927292168]
            else:
                error=[.016466185512,.000374231489]  # owner's Y gain 2.078
        elif label=='cycle_03' and self.outcome=='normal_gain':
            error=[.0201,.0216]
        elif label.startswith('gain_recheck_'):
            error=([.0162,.0009] if label.endswith('_01') else [.0160,.0011])
            if self.outcome=='unstable' and label.endswith('_01'):
                error=[-.0035,.0008]
            if self.outcome=='offset_tampering':
                self.printer.var['idex_xoffset']+=.001
        else:
            raise AssertionError('Unexpected first-write sequence '+label)
        drift=[.020,.002] if self.outcome=='return_drift' and label.startswith('gain_recheck_') else [.001,.002]
        self.record('measurements',label=label,error_mm=error,
                    t0_drift_mm=drift,scatter_estimate_mm=.002,
                    saved_offsets=self.offsets())
        return error


def make_sim(tmp,outcome):
    c=base.Config();s=ResponseSimulation(c,outcome);s.results=tmp
    c.p.var['auto_xy_enabled']=1
    return c,s


def run(outcome):
    with tempfile.TemporaryDirectory() as tmp:
        c,s=make_sim(tmp,outcome);baseline=s.offsets()
        cmd=base.Cmd()
        try:
            s.cmd_AUTO_XY_GATE(cmd)
        except ValueError as exc:
            return ('failed', str(exc), s.report, s, c, cmd, baseline)
        return ('passed', '', s.report, s, c, cmd, baseline)


def test_alignment_first_first_write():
    for outcome in ('first_confirmed','high_gain'):
        status,_,r,s,c,cmd,_=run(outcome)
        assert status=='passed',r
        assert r['status']=='CALIBRATED_AND_VAOC_EXITED'
        assert r['settling_status']=='STABLE'
        assert r['response_gain_recheck_status']=='CONFIRMED_IN_TOLERANCE'
        assert len(r['response_gain_anomalies'])==1
        assert len(r['response_gain_rechecks'])==2
        assert r['corrections_applied']==1 and r['verification_attempts']==2
        assert len(r['write_intents'])==1 and len(r['saved_corrections'])==1
        assert s.labels==['settle_01','settle_02','cycle_01','cycle_02',
                          'gain_recheck_02_01','gain_recheck_02_02'],s.labels
        assert s.vaoc is False and c.p.var['auto_xy_fault']==0
        assert any('WARNING: unusual offset response' in m for m in cmd.messages)
    print('PASS: first-write gain >2.0 logs warning and verifies stable aligned result without another write.')


def test_actual_second_write_2372():
    status,_,r,s,c,cmd,_=run('second_confirmed')
    assert status=='passed',r
    assert r['corrections_applied']==2 and r['verification_attempts']==2
    assert len(r['saved_corrections'])==2
    assert len(r['response_gain_anomalies'])==1
    anomaly=r['response_gain_anomalies'][0]
    assert anomaly['cycle']==3 and anomaly['axes']==[0],anomaly
    assert abs(anomaly['gains'][0]-2.372)<.004,anomaly
    assert max(abs(x) for x in r['final_error_mm'])<.030,r['final_error_mm']
    assert s.labels==['settle_01','settle_02','cycle_01','cycle_02','cycle_03',
                      'gain_recheck_03_01','gain_recheck_03_02'],s.labels
    assert s.vaoc is False and c.p.var['auto_xy_fault']==0
    assert any('WARNING: unusual offset response' in m for m in cmd.messages)
    print('PASS: owner second-write X gain 2.372 confirms twice then exits VAOC successfully.')


def test_normal_gain_remains_normal():
    status,_,r,s,c,cmd,_=run('normal_gain')
    assert status=='passed',r
    assert not r.get('response_gain_anomalies')
    assert not r.get('response_gain_rechecks')
    assert r['verification_attempts']==2
    assert r['status']=='CALIBRATED_AND_VAOC_EXITED'
    print('PASS: normal response still uses ordinary two consecutive verification passes.')


def test_safety_fail_closed():
    for outcome,reason,expected_writes in [
        ('out_of_tolerance','alignment outside tolerance',1),
        ('gross','gross/reverse',1),
        ('reverse','gross/reverse',1),
        ('unstable','repeat measurements not stable',1),
        ('offset_tampering','saved offsets changed',1),
        ('return_drift','repeat measurements not stable',1),
        ('second_outside','alignment outside tolerance',2),
        ('second_gross','gross/reverse',2),
        ('second_unstable','repeat measurements not stable',2),
    ]:
        status,error,r,s,c,_,baseline=run(outcome)
        assert status=='failed' and reason in error,(outcome,error)
        assert r['status']=='FAILED_DO_NOT_PRINT',outcome
        assert r['rollback'].startswith('ORIGINAL_SAVED_VALUES_RESTORED'),outcome
        assert max(abs(a-b) for a,b in zip(s.offsets(),baseline))<.000001,outcome
        assert c.p.var['auto_xy_fault']==1 and c.p.var['auto_xy_enabled']==0,outcome
        assert len(r['saved_corrections'])==expected_writes,outcome
        assert s.vaoc is True,outcome
    print('PASS: gross, reverse, out-of-tolerance, unstable and tampered results fault and rollback.')


def test_verification_budget_is_not_bypassed():
    with tempfile.TemporaryDirectory() as tmp:
        c,s=make_sim(tmp,'first_confirmed')
        s.max_verify=1
        try:s.cmd_AUTO_XY_GATE(base.Cmd())
        except ValueError as exc:assert 'insufficient verification budget' in str(exc)
        else:raise AssertionError('Exceeded verification budget')
        assert s.report['status']=='FAILED_DO_NOT_PRINT'
        assert not s.report.get('response_gain_rechecks')
    print('PASS: anomalous response never bypasses verification budget.')


if __name__=='__main__':
    test_alignment_first_first_write()
    test_actual_second_write_2372()
    test_normal_gain_remains_normal()
    test_safety_fail_closed()
    test_verification_budget_is_not_bypassed()
