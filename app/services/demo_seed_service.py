from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone

from sqlmodel import Session

from app.storage import repositories
from app.utils.time import utc_now


@dataclass(slots=True)
class DemoSeedSummary:
    days: int
    sleeps_saved: int
    recoveries_saved: int
    cycles_saved: int
    exams_saved: int


class DemoSeedService:
    def __init__(self, session: Session):
        self.session = session

    def seed(
        self, days: int = 30, seed: int = 42, include_exams: bool = True
    ) -> DemoSeedSummary:
        rng = random.Random(seed)
        tz = timezone(timedelta(hours=3))
        today = datetime.now(tz).date()
        start_day = today - timedelta(days=days - 1)

        sleeps_saved = 0
        recoveries_saved = 0
        cycles_saved = 0

        for offset in range(days):
            day = start_day + timedelta(days=offset)
            cycle_id = 88000000 + int(day.strftime("%Y%m%d"))

            workout_day = offset % 3 == 1 or offset % 7 == 5
            tough_day = offset in {days - 12, days - 5, days - 2}
            strain = self._strain(rng, workout_day=workout_day, tough_day=tough_day)
            sleep_minutes = self._sleep_minutes(rng, tough_day=tough_day)
            sleep_payload = self._sleep_payload(
                day=day,
                cycle_id=cycle_id,
                sleep_minutes=sleep_minutes,
                rng=rng,
                tz=tz,
            )
            recovery_payload = self._recovery_payload(
                sleep_payload=sleep_payload,
                strain=strain,
                sleep_minutes=sleep_minutes,
                rng=rng,
            )
            cycle_payload = self._cycle_payload(
                day=day,
                cycle_id=cycle_id,
                strain=strain,
                rng=rng,
                tz=tz,
            )

            repositories.upsert_sleep(self.session, sleep_payload)
            repositories.upsert_recovery(self.session, recovery_payload)
            repositories.upsert_cycle(self.session, cycle_payload)
            sleeps_saved += 1
            recoveries_saved += 1
            cycles_saved += 1

        exams_saved = 0
        if include_exams:
            exams_saved = self._seed_demo_exams(today=today, tz=tz)

        repositories.save_sync_run(
            self.session,
            source="demo",
            days=days,
            sleeps_saved=sleeps_saved,
            recoveries_saved=recoveries_saved,
            cycles_saved=cycles_saved,
            skipped_records=0,
            started_at=utc_now(),
            message="DEMO DATA generated locally; no WHOOP authentication used.",
        )
        return DemoSeedSummary(
            days=days,
            sleeps_saved=sleeps_saved,
            recoveries_saved=recoveries_saved,
            cycles_saved=cycles_saved,
            exams_saved=exams_saved,
        )

    def _sleep_minutes(self, rng: random.Random, *, tough_day: bool) -> int:
        baseline = rng.randint(405, 485)
        if tough_day:
            baseline -= rng.randint(100, 155)
        elif rng.random() < 0.15:
            baseline -= rng.randint(45, 90)
        return max(240, min(540, baseline))

    def _strain(self, rng: random.Random, *, workout_day: bool, tough_day: bool) -> float:
        if tough_day:
            return round(rng.uniform(13.8, 17.6), 1)
        if workout_day:
            return round(rng.uniform(9.5, 15.2), 1)
        return round(rng.uniform(4.0, 9.0), 1)

    def _sleep_payload(
        self,
        *,
        day,
        cycle_id: int,
        sleep_minutes: int,
        rng: random.Random,
        tz,
    ) -> dict:
        end = datetime.combine(
            day,
            time(hour=rng.randint(6, 8), minute=rng.choice([0, 10, 20, 30, 45])),
            tz,
        )
        awake_minutes = rng.randint(24, 62)
        in_bed_minutes = sleep_minutes + awake_minutes
        start = end - timedelta(minutes=in_bed_minutes)

        rem_minutes = int(sleep_minutes * rng.uniform(0.20, 0.26))
        slow_wave_minutes = int(sleep_minutes * rng.uniform(0.15, 0.22))
        light_minutes = sleep_minutes - rem_minutes - slow_wave_minutes
        sleep_performance = max(
            45, min(98, int((sleep_minutes / 480) * 100 + rng.randint(-5, 5)))
        )
        sleep_efficiency = max(74, min(96, int((sleep_minutes / in_bed_minutes) * 100)))

        return {
            "id": f"demo-sleep-{day.isoformat()}",
            "cycle_id": cycle_id,
            "user_id": 1001,
            "created_at": start.isoformat(),
            "updated_at": end.isoformat(),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "timezone_offset": "+03:00",
            "nap": False,
            "score_state": "SCORED",
            "source": "demo-seed",
            "score": {
                "sleep_performance_percentage": sleep_performance,
                "sleep_efficiency_percentage": sleep_efficiency,
                "sleep_consistency_percentage": rng.randint(62, 92),
                "respiratory_rate": round(rng.uniform(13.0, 16.8), 1),
                "stage_summary": {
                    "total_in_bed_time_milli": in_bed_minutes * 60000,
                    "total_light_sleep_time_milli": light_minutes * 60000,
                    "total_slow_wave_sleep_time_milli": slow_wave_minutes * 60000,
                    "total_rem_sleep_time_milli": rem_minutes * 60000,
                    "total_awake_time_milli": awake_minutes * 60000,
                },
            },
        }

    def _recovery_payload(
        self,
        *,
        sleep_payload: dict,
        strain: float,
        sleep_minutes: int,
        rng: random.Random,
    ) -> dict:
        sleep_penalty = max(0, 420 - sleep_minutes) / 5
        strain_penalty = max(0, strain - 10) * 3
        recovery_score = int(
            max(20, min(96, 78 - sleep_penalty - strain_penalty + rng.randint(-8, 8)))
        )
        hrv = round(
            max(22, min(78, 48 + (recovery_score - 65) * 0.55 + rng.uniform(-4.5, 4.5))),
            1,
        )
        rhr = int(
            max(
                47,
                min(
                    78,
                    58
                    + (65 - recovery_score) * 0.18
                    + max(0, strain - 12)
                    + rng.randint(-2, 3),
                ),
            )
        )

        return {
            "cycle_id": sleep_payload["cycle_id"],
            "sleep_id": sleep_payload["id"],
            "user_id": 1001,
            "created_at": sleep_payload["end"],
            "updated_at": sleep_payload["end"],
            "score_state": "SCORED",
            "source": "demo-seed",
            "score": {
                "recovery_score": recovery_score,
                "resting_heart_rate": rhr,
                "hrv_rmssd_milli": hrv,
                "spo2_percentage": round(rng.uniform(96.2, 99.2), 1),
                "skin_temp_celsius": round(rng.uniform(-0.4, 0.5), 1),
            },
        }

    def _cycle_payload(
        self, *, day, cycle_id: int, strain: float, rng: random.Random, tz
    ) -> dict:
        start = datetime.combine(day, time(hour=0, minute=0), tz)
        end = start + timedelta(hours=23, minutes=59)
        avg_hr = int(60 + strain * 2.2 + rng.randint(-4, 5))
        max_hr = int(max(avg_hr + 28, 122 + strain * 4.1 + rng.randint(-8, 8)))
        return {
            "id": cycle_id,
            "user_id": 1001,
            "created_at": start.isoformat(),
            "updated_at": end.isoformat(),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "timezone_offset": "+03:00",
            "score_state": "SCORED",
            "source": "demo-seed",
            "score": {
                "strain": strain,
                "kilojoule": round(7200 + strain * 430 + rng.uniform(-350, 350), 1),
                "average_heart_rate": avg_hr,
                "max_heart_rate": max_hr,
            },
        }

    def _seed_demo_exams(self, *, today, tz) -> int:
        exams = [
            (
                "Linear Algebra",
                datetime.combine(today - timedelta(days=1), time(hour=10), tz),
                72,
                "demo seeded past exam",
            ),
            (
                "Operating Systems",
                datetime.combine(today + timedelta(days=1), time(hour=13), tz),
                None,
                "demo seeded upcoming exam",
            ),
            (
                "Data Structures",
                datetime.combine(today + timedelta(days=4), time(hour=9, minute=30), tz),
                None,
                "demo seeded upcoming exam",
            ),
        ]
        for course, exam_at, grade, notes in exams:
            repositories.upsert_exam(
                self.session,
                course=course,
                exam_at=exam_at,
                grade=grade,
                notes=notes,
            )
        return len(exams)
