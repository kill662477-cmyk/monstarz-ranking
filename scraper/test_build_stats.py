import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import build_stats


class RecoveryPublishingTests(unittest.TestCase):
    def test_waiting_month_stays_visible_then_replaces_old_counts(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "data"
            public = Path(directory) / "public"
            data.mkdir()
            public.mkdir()
            (data / "recovery.json").write_text(json.dumps({
                "from_month": "2026-08", "month": "2026-09", "done": False,
            }))
            old = build_stats.build_one({"month": "2026-08", "counters": {"posts": 16336}})
            output = public / "monstarz_2026-08.json"
            output.write_text(json.dumps(old))
            with patch.object(build_stats, "DATA_DIR", str(data)), \
                 patch.object(build_stats, "PUB_DIR", str(public)):
                build_stats.main()
                pending = json.loads(output.read_text(encoding="utf-8"))
                self.assertTrue(pending["collection"]["recovery_pending"])
                self.assertEqual(pending["collection"]["posts_total"], 16336)
                months = json.loads((public / "months.json").read_text(encoding="utf-8"))
                self.assertEqual([item["month"] for item in months["months"]], ["2026-09", "2026-08"])
                (data / "state_2026-08.json").write_text(json.dumps({
                    "month": "2026-08", "counters": {"posts": 40}, "done": False,
                }))
                build_stats.main()
                rebuilt = json.loads(output.read_text(encoding="utf-8"))
                self.assertNotIn("recovery_pending", rebuilt["collection"])
                self.assertEqual(rebuilt["collection"]["posts_total"], 40)


if __name__ == "__main__":
    unittest.main()
