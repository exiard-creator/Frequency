import importlib.util
import sys
import unittest
from pathlib import Path


def load_module():
    module_path = Path(__file__).resolve().parents[1] / "src" / "known_tech_comparison.py"
    spec = importlib.util.spec_from_file_location("known_tech_comparison", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class KnownTechComparisonSmokeTests(unittest.TestCase):
    def test_module_loads_and_uses_repo_outputs(self) -> None:
        module = load_module()
        self.assertEqual(module.OUT_DIR, Path(__file__).resolve().parents[1] / "outputs")
        self.assertTrue(callable(module.run_comparison))


if __name__ == "__main__":
    unittest.main()
