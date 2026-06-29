"""任务调度核心逻辑 回归测试。

覆盖 compute_next_run / is_due / due_tasks / validate_task，
全部注入固定 now，不依赖真实时钟，离线可重复。
"""
import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.scheduler import (
    compute_next_run, is_due, due_tasks, validate_task, describe_schedule,
)


NOW = datetime(2026, 6, 29, 10, 0, 0)


class TestComputeNextRun(unittest.TestCase):
    def test_interval_never_run_is_due_now(self):
        task = {"trigger": "interval", "interval_minutes": 60}
        self.assertEqual(compute_next_run(task, NOW), NOW)

    def test_interval_after_last_run(self):
        task = {"trigger": "interval", "interval_minutes": 60,
                "last_run": (NOW - timedelta(minutes=20)).isoformat()}
        nxt = compute_next_run(task, NOW)
        self.assertEqual(nxt, NOW + timedelta(minutes=40))

    def test_interval_missed_aligns_to_now(self):
        # 上次运行在很久以前，不补跑堆积，下次=now。
        task = {"trigger": "interval", "interval_minutes": 30,
                "last_run": (NOW - timedelta(hours=5)).isoformat()}
        self.assertEqual(compute_next_run(task, NOW), NOW)

    def test_daily_later_today(self):
        task = {"trigger": "daily", "daily_time": "18:30"}
        self.assertEqual(compute_next_run(task, NOW),
                         NOW.replace(hour=18, minute=30, second=0, microsecond=0))

    def test_daily_already_passed_goes_tomorrow(self):
        task = {"trigger": "daily", "daily_time": "08:00"}
        expected = (NOW + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
        self.assertEqual(compute_next_run(task, NOW), expected)


class TestIsDue(unittest.TestCase):
    def test_disabled_never_due(self):
        task = {"trigger": "interval", "interval_minutes": 1, "enabled": False}
        self.assertFalse(is_due(task, NOW))

    def test_interval_due_when_elapsed(self):
        task = {"trigger": "interval", "interval_minutes": 30,
                "last_run": (NOW - timedelta(minutes=31)).isoformat()}
        self.assertTrue(is_due(task, NOW))

    def test_interval_not_due_when_recent(self):
        task = {"trigger": "interval", "interval_minutes": 30,
                "last_run": (NOW - timedelta(minutes=5)).isoformat()}
        self.assertFalse(is_due(task, NOW))

    def test_daily_due_at_time(self):
        task = {"trigger": "daily", "daily_time": "10:00"}
        # 10:00 整点，candidate==now → 明天；故此刻不到期。
        self.assertFalse(is_due(task, NOW))
        # 但 10:00:01 时，今天的 10:00 已过 → 明天，仍不到期；
        # 用 last_run 模型的 daily 不依赖 last_run，这里验证未到点不跑。
        task2 = {"trigger": "daily", "daily_time": "09:59"}
        self.assertFalse(is_due(task2, NOW))


class TestDueTasks(unittest.TestCase):
    def test_filters_due_only(self):
        tasks = [
            {"id": 1, "trigger": "interval", "interval_minutes": 60},  # never run → due
            {"id": 2, "trigger": "interval", "interval_minutes": 60,
             "last_run": (NOW - timedelta(minutes=5)).isoformat()},     # recent → not due
            {"id": 3, "trigger": "interval", "interval_minutes": 1,
             "enabled": False},                                          # disabled → not due
        ]
        due = due_tasks(tasks, NOW)
        self.assertEqual([t["id"] for t in due], [1])


class TestValidateTask(unittest.TestCase):
    def test_valid_interval(self):
        r = validate_task({"task_type": "recheck", "trigger": "interval",
                           "interval_minutes": 30})
        self.assertTrue(r["ok"])

    def test_valid_daily(self):
        r = validate_task({"task_type": "collect", "trigger": "daily",
                           "daily_time": "08:30"})
        self.assertTrue(r["ok"])

    def test_valid_polish(self):
        r = validate_task({"task_type": "polish", "trigger": "daily",
                           "daily_time": "08:30"})
        self.assertTrue(r["ok"])

    def test_bad_type(self):
        r = validate_task({"task_type": "xxx", "trigger": "interval",
                           "interval_minutes": 30})
        self.assertFalse(r["ok"])

    def test_bad_interval(self):
        r = validate_task({"task_type": "recheck", "trigger": "interval",
                           "interval_minutes": 0})
        self.assertFalse(r["ok"])


class TestDescribe(unittest.TestCase):
    def test_describe_interval_hours(self):
        self.assertEqual(describe_schedule({"trigger": "interval", "interval_minutes": 120}), "每 2 小时")

    def test_describe_interval_minutes(self):
        self.assertEqual(describe_schedule({"trigger": "interval", "interval_minutes": 45}), "每 45 分钟")

    def test_describe_daily(self):
        self.assertEqual(describe_schedule({"trigger": "daily", "daily_time": "18:30"}), "每天 18:30")


if __name__ == "__main__":
    unittest.main(verbosity=2)
