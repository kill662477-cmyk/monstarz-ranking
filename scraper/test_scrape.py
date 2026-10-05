import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import scrape


def post_html(receiver="this", author="42"):
    return (
        "<div id='contain_user_info'>"
        f'<a onclick="YG_COMMON.show_nick_dropdown({receiver}, \'0\', \'{author}\', \'N\')">Writer</a>'
        '<div class="date">2026-08-15 12:00:00</div>'
        '<span id="board_pan_monstarz_123_good">2</span>'
        "<div id='reply_list_layer'>"
        f'<a onclick="YG_COMMON.show_nick_dropdown({receiver}, \'0\', \'99\', \'N\')">Reader</a>'
        "reply_paging"
    )


class ScraperTests(unittest.TestCase):
    def test_old_and_current_markup(self):
        for receiver in ("this", "$(this)"):
            with self.subTest(receiver=receiver):
                rec = scrape.parse_post(123, post_html(receiver))
                self.assertEqual(rec["author_no"], "42")
                self.assertEqual(rec["comments"], [("99", "Reader")])
                self.assertEqual(rec["good"], 2)

    def test_unknown_author_markup_fails(self):
        with self.assertRaises(RuntimeError):
            scrape.parse_post(123, post_html(author="invalid"))

    def test_failed_voters_do_not_change_counts(self):
        state = {"members": {}, "counters": {"voter_fetches": 0}}
        with patch.object(scrape, "fetch_voters", return_value=None):
            with self.assertRaises(RuntimeError):
                scrape.fold(state, scrape.parse_post(123, post_html()), True, 0)
        self.assertEqual(state["members"], {})
        self.assertEqual(state["counters"]["voter_fetches"], 0)

    def test_failed_post_does_not_advance_cursor(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cursor.json"
            path.write_text('{"last_id":122}')
            with patch.object(scrape, "DATA_DIR", directory), \
                 patch.object(scrape, "current_max_id", return_value=123), \
                 patch.object(scrape, "http", return_value=(None, 403)):
                with self.assertRaises(RuntimeError):
                    scrape.run_update(0, False, 0)
            self.assertEqual(json.loads(path.read_text())["last_id"], 122)

    def test_saved_counts_not_duplicated_after_cursor_write_interruption(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(scrape, "DATA_DIR", directory):
                state = scrape.load_state("2026-08")
                state["last_processed_id"] = 123
                state["counters"]["posts"] = 1
                scrape.save_json(scrape.state_path("2026-08"), state)
                scrape.save_json(str(Path(directory) / "cursor.json"), {"last_id": 122})
                with patch.object(scrape, "current_max_id", return_value=123), \
                     patch.object(scrape, "http", return_value=(post_html(), 200)):
                    scrape.run_update(0, False, 0)
                self.assertEqual(scrape.load_state("2026-08")["counters"]["posts"], 1)

    def test_recovery_reset_only_once_and_preserve_earlier_months(self):
        with tempfile.TemporaryDirectory() as directory:
            data = Path(directory) / "data"
            public = Path(directory) / "public"
            data.mkdir()
            public.mkdir()
            (data / "state_2026-07.json").write_text('{"untouched":true}')
            (data / "state_2026-08.json").write_text('{"bad":true}')
            (public / "monstarz_2026-08.json").write_text('{}')
            with patch.object(scrape, "DATA_DIR", str(data)), \
                 patch.object(scrape, "PUB_DIR", str(public)), \
                 patch.object(scrape, "current_max_id", return_value=123), \
                 patch.object(scrape, "run_backfill", return_value={"done": False}):
                scrape.run_recover("2026-08", 0, False, 1)
                self.assertFalse((data / "state_2026-08.json").exists())
                self.assertFalse((public / "monstarz_2026-08.json").exists())
                self.assertTrue((data / "state_2026-07.json").exists())
                (data / "state_2026-08.json").write_text('{"rebuilt":true}')
                scrape.run_recover("2026-08", 0, False, 1)
                self.assertEqual(json.loads((data / "state_2026-08.json").read_text()), {"rebuilt": True})


if __name__ == "__main__":
    unittest.main()
