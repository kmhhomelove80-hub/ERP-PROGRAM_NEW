from datetime import date, datetime, timedelta, timezone
import unittest
import xml.etree.ElementTree as ET
import actas_schedule as schedule


class ScheduleTests(unittest.TestCase):
    def calendar(self, now):
        return dict(schema=1,timezone='Asia/Seoul',coverage_start='2026-01-01',coverage_end='2026-12-31',
                    confirmed_at=now.isoformat(),source='synthetic-policy-test-only',holidays={'2026-05-05':'어린이날'})
    def test_nine_hour_slots_and_weekends(self):
        for hour in range(24):
            now=datetime(2026,11,5,hour,0,tzinfo=schedule.KST)
            self.assertEqual(schedule.slot_decision(now,self.calendar(now))['due'],hour in range(9,18))
        for day in (7,8):
            now=datetime(2026,11,day,9,0,tzinfo=schedule.KST)
            self.assertEqual(schedule.slot_decision(now,self.calendar(now))['reason'],'weekend')
    def test_public_holiday_and_new_temporary_holiday_are_skipped(self):
        now=datetime(2026,5,5,9,0,tzinfo=schedule.KST)
        self.assertEqual(schedule.slot_decision(now,self.calendar(now))['reason'],'holiday')
        now=datetime(2026,11,5,9,0,tzinfo=schedule.KST);calendar=self.calendar(now)
        calendar['holidays']['2026-11-05']='합성 임시휴일 테스트'
        self.assertEqual(schedule.slot_decision(now,calendar)['reason'],'holiday')
    def test_expired_unknown_or_incomplete_calendar_never_defaults_to_workday(self):
        now=datetime(2026,11,5,9,0,tzinfo=schedule.KST)
        bad=[None,{},self.calendar(now-timedelta(hours=25))]
        invalid=self.calendar(now);invalid['coverage_end']='2026-10-31';bad.append(invalid)
        for calendar in bad:self.assertEqual(schedule.slot_decision(now,calendar)['reason'],'holiday_calendar_unverified')
    def test_timezone_and_missed_slot_are_handled(self):
        utc=datetime(2026,11,5,0,0,tzinfo=timezone.utc)
        self.assertTrue(schedule.slot_decision(utc,self.calendar(utc))['due'])
        late=utc+timedelta(minutes=1)
        self.assertFalse(schedule.slot_decision(late,self.calendar(late))['due'])
        self.assertFalse(schedule.slot_decision(utc.replace(tzinfo=None),self.calendar(utc))['due'])
    def test_task_template_has_nine_weekday_triggers_and_cannot_run_before_verified_setup(self):
        xml=schedule.disabled_task_xml(executable=r'C:\App\python.exe',launcher=r'C:\Tools\run.py',sid='S-1-5-21-123',first_day=date(2026,11,5))
        root=ET.fromstring(xml);ns={'t':'http://schemas.microsoft.com/windows/2004/02/mit/task'}
        triggers=root.findall('t:Triggers/t:CalendarTrigger',ns)
        self.assertEqual(len(triggers),9)
        self.assertEqual([t.find('t:StartBoundary',ns).text[-8:] for t in triggers],[f'{h:02}:00:00' for h in range(9,18)])
        self.assertEqual(root.find('t:Settings/t:Enabled',ns).text,'false')
        self.assertEqual(root.find('t:Settings/t:MultipleInstancesPolicy',ns).text,'IgnoreNew')
        self.assertEqual(root.find('t:Settings/t:StartWhenAvailable',ns).text,'false')
        for trigger in triggers:
            days=trigger.find('t:ScheduleByWeek/t:DaysOfWeek',ns)
            self.assertEqual(len(days),5)


if __name__=='__main__':unittest.main(verbosity=2)
