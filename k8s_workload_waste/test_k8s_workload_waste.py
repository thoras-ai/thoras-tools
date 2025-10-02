"""
Unit tests for waste_calc.py

Run with: pytest -v .
"""

import unittest
from .k8s_workload_waste import ResourceParser, WasteCalculator, NodeConfig, ResourceMetrics


class TestResourceParser(unittest.TestCase):
    """Test resource string parsing"""

    def test_parse_cpu_formats(self):
        """Test CPU parsing handles all Kubernetes CPU formats correctly"""
        parser = ResourceParser()

        # Test millicores
        self.assertAlmostEqual(parser.parse_cpu("500m"), 0.5)
        self.assertAlmostEqual(parser.parse_cpu("1500m"), 1.5)

        # Test nanocores
        self.assertAlmostEqual(parser.parse_cpu("1000000000n"), 1.0)

        # Test cores
        self.assertAlmostEqual(parser.parse_cpu("2"), 2.0)
        self.assertAlmostEqual(parser.parse_cpu("4.5"), 4.5)

        # Test edge cases
        self.assertEqual(parser.parse_cpu("0"), 0.0)
        self.assertEqual(parser.parse_cpu(""), 0.0)
        self.assertEqual(parser.parse_cpu("invalid"), 0.0)

    def test_parse_memory_formats(self):
        """Test memory parsing handles all Kubernetes memory formats correctly"""
        parser = ResourceParser()

        # Test Ki (kibibytes)
        self.assertAlmostEqual(parser.parse_memory("1048576Ki"), 1.0)

        # Test Mi (mebibytes)
        self.assertAlmostEqual(parser.parse_memory("1024Mi"), 1.0)
        self.assertAlmostEqual(parser.parse_memory("2048Mi"), 2.0)

        # Test Gi (gibibytes)
        self.assertAlmostEqual(parser.parse_memory("4Gi"), 4.0)
        self.assertAlmostEqual(parser.parse_memory("0.5Gi"), 0.5)

        # Test edge cases
        self.assertEqual(parser.parse_memory("0"), 0.0)
        self.assertEqual(parser.parse_memory(""), 0.0)
        self.assertEqual(parser.parse_memory("invalid"), 0.0)


class TestWasteCalculator(unittest.TestCase):
    """Test waste calculation logic"""

    def setUp(self):
        """Set up test fixtures"""
        self.node_config = NodeConfig(
            cpu_cores=4.0,
            memory_gb=16.0,
            cost_per_hour=0.20
        )
        self.calculator = WasteCalculator(self.node_config)

    def test_waste_calculation_with_overallocation(self):
        """Test waste is correctly calculated when resources are over-allocated"""
        # Pod requesting 1 core but only using 0.45 cores (50% utilization)
        # Target allocation for 90% utilization = 0.45 / 0.9 = 0.5 cores
        # Waste = 1.0 - 0.5 = 0.5 cores
        request_data = {
            "default/test-pod": ResourceMetrics(cpu=1.0, memory=2.0)
        }
        usage_data = {
            "default/test-pod": ResourceMetrics(cpu=0.45, memory=0.9)
        }

        workload_waste, total_cpu_waste, total_memory_waste, total_cost_waste = (
            self.calculator.calculate(request_data, usage_data)
        )

        # Verify CPU waste
        self.assertAlmostEqual(total_cpu_waste, 0.5, places=2)

        # Verify memory waste (2.0 - 0.9/0.9 = 2.0 - 1.0 = 1.0)
        self.assertAlmostEqual(total_memory_waste, 1.0, places=2)

        # Verify cost waste is positive
        self.assertGreater(total_cost_waste, 0)

        # Verify workload aggregation
        self.assertIn("default/test", workload_waste)
        self.assertEqual(workload_waste["default/test"].pod_count, 1)

    def test_no_waste_at_90_percent_utilization(self):
        """Test that no waste is calculated when pod is at exactly 90% utilization"""
        # Pod at exactly 90% utilization should have minimal waste
        request_data = {
            "default/efficient-pod": ResourceMetrics(cpu=1.0, memory=2.0)
        }
        usage_data = {
            "default/efficient-pod": ResourceMetrics(cpu=0.9, memory=1.8)
        }

        workload_waste, total_cpu_waste, total_memory_waste, total_cost_waste = (
            self.calculator.calculate(request_data, usage_data)
        )

        # At 90% utilization, waste should be approximately zero
        self.assertAlmostEqual(total_cpu_waste, 0.0, places=2)
        self.assertAlmostEqual(total_memory_waste, 0.0, places=2)
        self.assertAlmostEqual(total_cost_waste, 0.0, places=4)
