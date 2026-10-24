#!/usr/bin/env python3
"""Offline homing prerequisite checks: never fault-latch before VAOC starts."""
import tempfile
import test_stage6_offline as base


class HomingSim(base.SimGate):
    def __init__(self, cfg, axes=''):
        super().__init__(cfg)
        self.axes=axes
    def sts(self,name):
        if name=='toolhead':
            return {'homed_axes':self.axes}
        return super().sts(name)


def assert_no_start(c, s, offsets, flags):
    assert not s.busy and not s.vaoc
    assert s.sim_commands == [],s.sim_commands
    assert s.offsets()==offsets
    assert {k:c.p.var.get(k,0) for k in flags}==flags
    assert not list(__import__('pathlib').Path(s.results).iterdir())


def test_manual_unhomed_is_info_only():
    for axes in ('', 'x', 'xy', 'xz', 'yz'):
        with tempfile.TemporaryDirectory() as tmp:
            c=base.Config();s=HomingSim(c,axes);s.results=tmp
            c.p.var['auto_xy_enabled']=1
            flags={'auto_xy_fault':0,'auto_xy_in_progress':0,'auto_xy_enabled':1}
            offsets=s.offsets()
            cmd=base.Cmd(mode='IDLE')
            s.cmd_AUTO_XY_GATE(cmd)
            assert len(cmd.messages)==1 and 'Printer must be homed first' in cmd.messages[0]
            assert_no_start(c,s,offsets,flags)
    print('PASS: manual unhomed calibration shows homing notice, no fault, VAOC or writes.')


def test_print_unhomed_stops_without_latch():
    with tempfile.TemporaryDirectory() as tmp:
        c=base.Config();s=HomingSim(c,'xy');s.results=tmp
        c.p.var['auto_xy_enabled']=1
        s.pending_print_mode='INDEPENDENT';s.pending_toolshifts=3
        flags={'auto_xy_fault':0,'auto_xy_in_progress':0,'auto_xy_enabled':1}
        offsets=s.offsets()
        try:s.cmd_AUTO_XY_GATE(base.Cmd(mode='PRINT'))
        except ValueError as e:
            assert 'Printer must be homed first' in str(e)
            assert 'Print start stopped' in str(e)
        else:raise AssertionError('An unhomed dual-tool print was allowed through')
        assert_no_start(c,s,offsets,flags)
        assert s.pending_print_mode is None and s.active_print_mode is None
    print('PASS: unhomed two-tool PRINT stops safely, without a persistent fault or writes.')


def test_bypass_and_existing_fault_stay_intact():
    with tempfile.TemporaryDirectory() as tmp:
        c=base.Config();s=HomingSim(c,'');s.results=tmp
        # OFF must bypass even with no homing.
        cmd=base.Cmd();s.cmd_AUTO_XY_GATE(cmd)
        assert any('bypassing' in m for m in cmd.messages)
        c.p.var['auto_xy_enabled']=1
        for mode in ('SINGLE_TOOL','COPY','MIRROR'):
            s.pending_print_mode=mode;s.pending_toolshifts=0
            cmd=base.Cmd(mode='PRINT');s.cmd_AUTO_XY_GATE(cmd)
            assert any('skipping VAOC' in m for m in cmd.messages)
        c.p.var['auto_xy_fault']=1
        try:s.cmd_AUTO_XY_GATE(base.Cmd())
        except ValueError as e:assert 'fault latched' in str(e)
        else:raise AssertionError('Existing safety fault bypassed by homing notice')
        assert c.p.var['auto_xy_fault']==1
        assert not s.sim_commands
    print('PASS: OFF/single-tool/COPY/MIRROR bypass unchanged; existing fault remains latched.')


def test_homing_loss_after_start_still_faults():
    with tempfile.TemporaryDirectory() as tmp:
        c=base.Config();s=HomingSim(c,'xyz');s.results=tmp
        c.p.var['auto_xy_enabled']=1
        baseline=s.offsets()
        original=s.worker_capture
        def lose_home(label):
            s.axes='xy'
            return original(label)
        s.worker_capture=lose_home
        try:s.cmd_AUTO_XY_GATE(base.Cmd())
        except ValueError as e: assert 'Axes no longer homed' in str(e)
        else:raise AssertionError('Lost homing during calibration did not fault')
        assert c.p.var['auto_xy_fault']==1
        assert c.p.var['auto_xy_in_progress']==1
        assert c.p.var['auto_xy_enabled']==0
        assert s.offsets()==baseline
    print('PASS: unexpected loss of homing after VAOC starts remains a latched safety fault.')


def test_status_macro_on_off_wording():
    from pathlib import Path
    cfg = (Path(__file__).resolve().parent / 'auto_xy_print.cfg').read_text()
    status = cfg.split('[gcode_macro AUTO_XY_STATUS]', 1)[1].split('[gcode_macro ', 1)[0]
    assert '"ON" if state else "OFF"' in status
    assert '"Calibration enabled for eligible dual-tool independent IDEX prints." if state' in status
    assert '"Calibration bypassed; saved RatOS offsets unchanged."' in status
    assert 'Latched faults still require safe recovery and acknowledgement.' in status
    assert '. OFF retains RatOS offsets but cannot bypass a latched fault.' not in status
    print('PASS: ON/OFF status explains current mode; safety-fault warning remains.')


if __name__=='__main__':
    test_manual_unhomed_is_info_only()
    test_print_unhomed_stops_without_latch()
    test_bypass_and_existing_fault_stay_intact()
    test_homing_loss_after_start_still_faults()
    test_status_macro_on_off_wording()
