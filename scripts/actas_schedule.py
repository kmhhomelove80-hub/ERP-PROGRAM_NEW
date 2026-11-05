"""Policy for future verified automation; never installs/enables a task itself.

There is deliberately no guessed holiday list. An expired/unverified calendar
blocks scheduled execution. UI automation must be verified before task setup.
"""
from datetime import date, datetime, timedelta, timezone
from pathlib import PureWindowsPath
import re
import xml.etree.ElementTree as ET

KST = timezone(timedelta(hours=9))
HOURS = tuple(range(9, 18))


def slot_decision(now, calendar):
    if not isinstance(now, datetime) or now.tzinfo is None:
        return dict(due=False, reason='clock_unverified')
    local = now.astimezone(KST)
    if local.weekday() >= 5:return dict(due=False, reason='weekend')
    if local.hour not in HOURS or local.minute != 0:
        return dict(due=False, reason='outside_hourly_slot')
    try:
        if calendar['schema'] != 1 or calendar['timezone'] != 'Asia/Seoul':raise ValueError
        start, end = date.fromisoformat(calendar['coverage_start']), date.fromisoformat(calendar['coverage_end'])
        checked = datetime.fromisoformat(calendar['confirmed_at'])
        if checked.tzinfo is None or not timedelta(0) <= now - checked <= timedelta(hours=24):raise ValueError
        if not start <= local.date() <= end:raise ValueError
        holidays = calendar['holidays']
        if not isinstance(holidays, dict):raise ValueError
        for day, label in holidays.items():
            if not start <= date.fromisoformat(day) <= end or not isinstance(label, str) or not label.strip():raise ValueError
        if not calendar.get('source'):raise ValueError
    except (KeyError, TypeError, ValueError, AttributeError):
        return dict(due=False, reason='holiday_calendar_unverified')
    if local.date().isoformat() in holidays:
        return dict(due=False, reason='holiday', holiday=holidays[local.date().isoformat()])
    return dict(due=True, reason='hourly_business_slot', slot=local.replace(minute=0, second=0, microsecond=0).isoformat())


def disabled_task_xml(*, executable, launcher, sid, first_day):
    """Nine explicit weekday triggers avoid repetition end-boundary ambiguity."""
    for path in (executable, launcher):
        if not PureWindowsPath(path).is_absolute() or any(ch in str(path) for ch in ('"', '\r', '\n', '\x00')):
            raise ValueError('기존 ERP의 절대 실행 경로가 필요합니다.')
    if not re.fullmatch(r'S-\d+(?:-\d+)+', sid):raise ValueError('현재 Windows 계정 SID가 필요합니다.')
    if not isinstance(first_day, date):raise ValueError('실행 시작 날짜가 필요합니다.')
    ns = 'http://schemas.microsoft.com/windows/2004/02/mit/task'
    ET.register_namespace('', ns)
    def node(parent, tag, text=None, **attrs):
        element = ET.SubElement(parent, '{' + ns + '}' + tag, attrs)
        if text is not None:element.text = text
        return element
    root = ET.Element('{' + ns + '}Task', {'version': '1.2'})
    triggers = node(root, 'Triggers')
    for hour in HOURS:
        trigger = node(triggers, 'CalendarTrigger')
        node(trigger, 'StartBoundary', first_day.isoformat() + f'T{hour:02}:00:00')
        node(trigger, 'Enabled', 'true')
        week = node(trigger, 'ScheduleByWeek');node(week, 'WeeksInterval', '1')
        days = node(week, 'DaysOfWeek')
        for day in ('Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday'):node(days, day)
    principal = node(node(root, 'Principals'), 'Principal', id='CurrentOwner')
    node(principal, 'UserId', sid);node(principal, 'LogonType', 'InteractiveToken');node(principal, 'RunLevel', 'LeastPrivilege')
    settings = node(root, 'Settings')
    for tag, text in dict(MultipleInstancesPolicy='IgnoreNew', DisallowStartIfOnBatteries='false',
                          StopIfGoingOnBatteries='false', StartWhenAvailable='false', AllowStartOnDemand='true',
                          Enabled='false', ExecutionTimeLimit='PT30M').items():node(settings, tag, text)
    action = node(node(root, 'Actions', Context='CurrentOwner'), 'Exec')
    node(action, 'Command', str(executable));node(action, 'Arguments', '"' + str(launcher) + '" --scheduled')
    node(action, 'WorkingDirectory', str(PureWindowsPath(launcher).parent))
    return ET.tostring(root, encoding='unicode', xml_declaration=True)
