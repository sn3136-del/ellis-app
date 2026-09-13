"""A running timer retains its installed next slot without inventing a cadence."""
from types import SimpleNamespace
import subprocess
import pytest
from app import main

CALENDAR = '*-*-* 00/6:20:00'
NEXT = 'Mon 2099-09-14 00:20:00 UTC'
ISO = '2099-09-14T00:20:00+00:00'


def timer(*, active='active', loaded='loaded', next_at='', calendar=CALENDAR):
    return '\n'.join([
        f'LoadState={loaded}', f'ActiveState={active}', f'NextElapseUSecRealtime={next_at}',
        'TimersCalendar=' + (f'{{ OnCalendar={calendar} ; next_elapse=Sun 2026-09-13 18:20:00 UTC }}' if calendar else ''),
        'RandomizedDelayUSec=10min', 'AccuracyUSec=1min'])


def command_results(monkeypatch, state, output='Next elapse: ' + NEXT, *, code=0, failure=None):
    calls = []
    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if argv[0] == 'systemctl':
            return SimpleNamespace(returncode=0, stdout=state)
        if failure:
            raise failure
        assert argv[:3] == ['systemd-analyze', 'calendar', '--iterations=1']
        return SimpleNamespace(returncode=code, stdout=output)
    monkeypatch.setattr(subprocess, 'run', run)
    return calls


def test_running_timer_uses_installed_calendar_and_reports_nominal_jitter(monkeypatch):
    calls = command_results(monkeypatch, timer())
    assert main._sweep_timer_status() == {'status': 'active', 'next_sweep_at': ISO,
        'schedule_basis': 'calendar', 'randomized_delay_seconds': 600, 'accuracy_seconds': 60}
    assert calls[1][0][-2:] == ['--', CALENDAR]
    assert calls[1][0][3].startswith('--base-time=')
    assert all(options['timeout'] == 2 and options['env']['LC_ALL'] == 'C' for _, options in calls)


def test_future_actual_timer_timestamp_wins_over_calendar(monkeypatch):
    calls = command_results(monkeypatch, timer(next_at='Mon 2099-09-14 00:27:12 UTC'))
    assert main._sweep_timer_status() == {'status': 'active', 'next_sweep_at': '2099-09-14T00:27:12+00:00', 'schedule_basis': 'timer'}
    assert len(calls) == 1


@pytest.mark.parametrize('raw', ['', 'n/a', '0', 'Sun 2026-01-01 00:00:00 UTC', 'bad timestamp'])
def test_blank_stale_or_unparseable_actual_timestamp_uses_calendar(monkeypatch, raw):
    calls = command_results(monkeypatch, timer(next_at=raw))
    assert main._next_sweep_at() == ISO and len(calls) == 2


@pytest.mark.parametrize('state', [timer(active='inactive'), timer(active='failed'), timer(loaded='not-found')])
def test_disabled_or_missing_unit_never_gets_calendar_countdown(monkeypatch, state):
    calls = command_results(monkeypatch, state)
    assert main._sweep_timer_status() == {'status': 'inactive', 'next_sweep_at': None}
    assert len(calls) == 1


@pytest.mark.parametrize('output,code', [('', 0), ('Next elapse: never', 0), ('Next elapse: Sun 2026-01-01 00:00:00 UTC', 0),
    ('Next elapse: ' + NEXT, 1), ('Next elapse: Mon 2099-09-14 00:20:00 UNKNOWN', 0)])
def test_unsupported_failed_or_past_calendar_does_not_invent_next_slot(monkeypatch, output, code):
    command_results(monkeypatch, timer(), output, code=code)
    assert main._sweep_timer_status() == {'status': 'active', 'next_sweep_at': None}


@pytest.mark.parametrize('failure', [FileNotFoundError(), subprocess.TimeoutExpired('systemd-analyze', 2)])
def test_calendar_failure_preserves_known_active_state(monkeypatch, failure):
    command_results(monkeypatch, timer(), failure=failure)
    assert main._sweep_timer_status() == {'status': 'active', 'next_sweep_at': None}


def test_no_installed_calendar_has_no_fallback(monkeypatch):
    calls = command_results(monkeypatch, timer(calendar=''))
    assert main._next_sweep_at() is None and len(calls) == 1


def test_host_and_explicit_calendar_timezone_are_preserved(monkeypatch):
    monkeypatch.setenv('TZ', 'Pacific/Honolulu')
    calendar = '*-*-* 06:20:00 Europe/Berlin'
    calls = command_results(monkeypatch, timer(calendar=calendar),
        'Next elapse: Mon 2099-09-14 06:20:00 CEST\n   (in UTC): Mon 2099-09-14 04:20:00 UTC')
    assert main._next_sweep_at() == '2099-09-14T04:20:00+00:00'
    assert calls[1][0][-1] == calendar and 'TZ' not in calls[1][1]['env']


def test_multiple_installed_schedules_choose_earliest_actual_slot(monkeypatch):
    state = timer().replace('RandomizedDelayUSec=',
        'TimersExtraIgnored=not a schedule\nRandomizedDelayUSec=')
    state = state.replace('TimersCalendar={', 'TimersCalendar={ OnCalendar=Mon *-*-* 12:20:00 ; next_elapse=n/a } {')
    calls = command_results(monkeypatch, state,
        'Next elapse: Mon 2099-09-14 12:20:00 UTC\n\nNext elapse: ' + NEXT)
    assert main._next_sweep_at() == ISO
    assert calls[1][0][-2:] == ['Mon *-*-* 12:20:00', CALENDAR]


def test_unknown_delay_is_not_reported_as_zero(monkeypatch):
    command_results(monkeypatch, timer().replace('10min', 'infinity'))
    assert main._sweep_timer_status()['randomized_delay_seconds'] is None
