from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from experiments.report import percentile, summarize
from experiments.run import cases


class ExperimentTests(unittest.TestCase):
    def test_failures_do_not_become_fast_successes(self):
        common = dict(case="1", suite="load", target="resolver", mode="recursive", cache="cold",
                      workers="4", protected="False", rate="10", attack_rate="0", sources="0",
                      repetition="1", phase="measure", traffic="legitimate", duration_s="2")
        result = summarize([{**common, "status": "SUCCESS", "latency_ms": "4"},
                            {**common, "status": "TIMEOUT", "latency_ms": ""}])
        self.assertEqual(result["failure_pct"], 50)
        self.assertEqual(result["successful_qps"], .5)
        self.assertEqual(result["p95_ms"], 4)

    def test_empty_percentile_is_missing(self):
        self.assertIsNone(percentile([], .95))
        self.assertEqual(percentile([0, 10], .95), 9.5)

    def test_full_matrix_covers_every_server(self):
        matrix = list(cases(False, ["dos", "ddos", "load"]))
        for suite in ("dos", "ddos", "load"):
            self.assertEqual({c[1] for c in matrix if c[0] == suite},
                             {"root", "tld", "auth", "resolver"})


if __name__ == "__main__":
    unittest.main()
