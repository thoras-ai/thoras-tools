#!/usr/bin/env python3
"""
Cluster waste calculation using Python
"""

import argparse
import json
import subprocess
from dataclasses import dataclass
from typing import Dict

# Default values (can be overridden by CLI arguments)
DEFAULT_NODE_CPU_CORES = 4  # Total CPU cores per node
DEFAULT_NODE_MEMORY_GB = 17  # Total memory in GB per node
DEFAULT_NODE_COST_PER_HOUR = 0.192  # Cost per node per hour in dollars


@dataclass
class ResourceMetrics:
    """Resource metrics for CPU and memory"""
    cpu: float
    memory: float


@dataclass
class WorkloadWaste:
    """Waste metrics for a workload"""
    cpu_waste: float
    memory_waste: float
    cost_waste: float
    pod_count: int


class ResourceParser:
    """Parses Kubernetes resource strings"""

    @staticmethod
    def parse_cpu(cpu_str: str) -> float:
        """Parse CPU string to cores"""
        if not cpu_str or cpu_str == "0":
            return 0.0

        if cpu_str.endswith("n"):
            return float(cpu_str[:-1]) / 1_000_000_000
        elif cpu_str.endswith("m"):
            return float(cpu_str[:-1]) / 1_000
        elif cpu_str.isdigit():
            return float(cpu_str)
        else:
            try:
                return float(cpu_str)
            except ValueError:
                return 0.0

    @staticmethod
    def parse_memory(mem_str: str) -> float:
        """Parse memory string to GB"""
        if not mem_str or mem_str == "0":
            return 0.0

        if mem_str.endswith("Ki"):
            return float(mem_str[:-2]) / 1_048_576  # Ki to GB
        elif mem_str.endswith("Mi"):
            return float(mem_str[:-2]) / 1_024  # Mi to GB
        elif mem_str.endswith("Gi"):
            return float(mem_str[:-2])  # Gi to GB
        elif mem_str.isdigit():
            return float(mem_str) / 1_073_741_824  # bytes to GB
        else:
            try:
                return float(mem_str) / 1_073_741_824
            except ValueError:
                return 0.0


class KubernetesClient:
    """Fetches data from Kubernetes cluster"""

    def __init__(self):
        self.parser = ResourceParser()

    def get_pod_usage(self) -> Dict[str, ResourceMetrics]:
        """Get current pod usage from metrics API"""
        try:
            result = subprocess.run(
                ["kubectl", "get", "--raw", "/apis/metrics.k8s.io/v1beta1/pods"],
                capture_output=True,
                text=True,
                check=True,
            )

            metrics = json.loads(result.stdout)
            usage = {}

            for pod in metrics.get("items", []):
                pod_key = f"{pod['metadata']['namespace']}/{pod['metadata']['name']}"

                total_cpu = 0.0
                total_memory = 0.0

                for container in pod.get("containers", []):
                    cpu_usage = container.get("usage", {}).get("cpu", "0")
                    memory_usage = container.get("usage", {}).get("memory", "0")

                    total_cpu += self.parser.parse_cpu(cpu_usage)
                    total_memory += self.parser.parse_memory(memory_usage)

                usage[pod_key] = ResourceMetrics(cpu=total_cpu, memory=total_memory)

            return usage

        except Exception as e:
            print(f"Error getting pod usage: {e}")
            return {}

    def get_pod_requests(self) -> Dict[str, ResourceMetrics]:
        """Get pod resource requests"""
        try:
            result = subprocess.run(
                ["kubectl", "get", "pods", "-A", "-ojson"],
                capture_output=True,
                text=True,
                check=True,
            )

            pods = json.loads(result.stdout)
            requests = {}

            for pod in pods.get("items", []):
                pod_key = f"{pod['metadata']['namespace']}/{pod['metadata']['name']}"

                total_cpu_req = 0.0
                total_memory_req = 0.0
                has_requests = False

                for container in pod.get("spec", {}).get("containers", []):
                    resources = container.get("resources", {})
                    container_requests = resources.get("requests", {})

                    if container_requests:
                        has_requests = True
                        cpu_req = container_requests.get("cpu", "0")
                        memory_req = container_requests.get("memory", "0")

                        total_cpu_req += self.parser.parse_cpu(cpu_req)
                        total_memory_req += self.parser.parse_memory(memory_req)

                # Only include pods that have resource requests
                if has_requests:
                    requests[pod_key] = ResourceMetrics(cpu=total_cpu_req, memory=total_memory_req)

            return requests

        except Exception as e:
            print(f"Error getting pod requests: {e}")
            return {}


class NodeConfig:
    """Node configuration for cost calculations"""

    def __init__(self, cpu_cores: float, memory_gb: float, cost_per_hour: float):
        self.cpu_cores = cpu_cores
        self.memory_gb = memory_gb
        self.cost_per_hour = cost_per_hour
        # Assume 65% of cost of a node is CPU, 35% is memory
        self.cpu_cost_per_hour = cost_per_hour * 0.65 / cpu_cores
        self.memory_cost_per_hour = cost_per_hour * 0.35 / memory_gb


class WasteCalculator:
    """Calculates resource waste for pods and workloads"""

    UTILIZATION_TARGET = 0.9

    def __init__(self, node_config: NodeConfig):
        self.node_config = node_config

    @staticmethod
    def extract_workload_name(pod_key: str) -> str:
        """Extract workload name from pod key by removing hash suffixes"""
        namespace, pod_name = pod_key.split("/", 1)
        workload_parts = pod_name.rsplit("-", 2)
        if len(workload_parts) >= 2:
            return f"{namespace}/{workload_parts[0]}"
        return pod_key

    def calculate(
        self,
        request_data: Dict[str, ResourceMetrics],
        usage_data: Dict[str, ResourceMetrics]
    ) -> tuple[Dict[str, WorkloadWaste], float, float, float]:
        """Calculate waste for each pod and aggregate by workload"""
        workload_waste = {}
        total_cpu_waste = 0.0
        total_memory_waste = 0.0
        total_cost_waste = 0.0

        for pod_key, req in request_data.items():
            usage = usage_data.get(pod_key, ResourceMetrics(cpu=0.0, memory=0.0))

            # Calculate waste assuming 90% utilization target
            # Target allocation = usage / 0.9 (what allocation should be for 90% utilization)
            # Waste = current allocation - target allocation
            cpu_target = usage.cpu / self.UTILIZATION_TARGET
            memory_target = usage.memory / self.UTILIZATION_TARGET

            cpu_waste = max(0, req.cpu - cpu_target)
            memory_waste = max(0, req.memory - memory_target)

            cost_waste = (
                cpu_waste * self.node_config.cpu_cost_per_hour +
                memory_waste * self.node_config.memory_cost_per_hour
            )

            total_cpu_waste += cpu_waste
            total_memory_waste += memory_waste
            total_cost_waste += cost_waste

            # Aggregate by workload
            workload_name = self.extract_workload_name(pod_key)
            if workload_name not in workload_waste:
                workload_waste[workload_name] = WorkloadWaste(
                    cpu_waste=0.0,
                    memory_waste=0.0,
                    cost_waste=0.0,
                    pod_count=0
                )

            workload_waste[workload_name].cpu_waste += cpu_waste
            workload_waste[workload_name].memory_waste += memory_waste
            workload_waste[workload_name].cost_waste += cost_waste
            workload_waste[workload_name].pod_count += 1

        return workload_waste, total_cpu_waste, total_memory_waste, total_cost_waste


class WasteReporter:
    """Generates waste analysis reports"""

    def print_report(
        self,
        workload_waste: Dict[str, WorkloadWaste],
        total_cpu_waste: float,
        total_memory_waste: float,
        total_cost_waste: float,
        pod_count: int
    ):
        """Print waste analysis report"""
        # Convert to list and sort by cost waste (highest first)
        waste_data = [
            {
                "workload": workload,
                "waste": waste
            }
            for workload, waste in workload_waste.items()
            if waste.cost_waste > 0.01  # Only show significant wasters
        ]
        waste_data.sort(key=lambda x: x["waste"].cost_waste, reverse=True)

        # Display top wasters
        if waste_data:
            print("=== TOP WASTE CONTRIBUTORS (by workload) ===")
            for item in waste_data[:10]:  # Top 10
                waste = item["waste"]
                annual_waste = waste.cost_waste * 24 * 365
                print(
                    f"{item['workload']:<40} CPU: {waste.cpu_waste:6.2f} cores  "
                    f"Memory: {waste.memory_waste:6.2f} GB  "
                    f"Annual waste: ${annual_waste:,.0f} "
                    f"[{waste.pod_count} pods]"
                )
            print("")

        # Summary
        annual_waste = total_cost_waste * 24 * 365

        print("=== CLUSTER WASTE SUMMARY ===")
        print(f"Pods analyzed: {pod_count}")
        print(f"Total CPU waste: {total_cpu_waste:.2f} cores")
        print(f"Total memory waste: {total_memory_waste:.2f} GB")
        print(f"Hourly cost waste: ${total_cost_waste:.2f}")
        print(f"💰 ANNUAL cost waste: ${annual_waste:,.0f}")

        if pod_count > 0:
            print(f"\nEfficiency insight: Consider right-sizing the high-waste pods above")

        print("✅ Analysis complete!")


class ClusterWasteAnalyzer:
    """Main orchestrator for cluster waste analysis"""

    def __init__(self, node_config: NodeConfig):
        self.node_config = node_config
        self.k8s_client = KubernetesClient()
        self.calculator = WasteCalculator(node_config)
        self.reporter = WasteReporter()

    def analyze(self):
        """Run complete waste analysis"""
        print("🔍 Calculating cluster waste...")
        print(
            f"Node specs: {self.node_config.cpu_cores} cores, "
            f"{self.node_config.memory_gb}GB, ${self.node_config.cost_per_hour}/hour"
        )
        print(
            f"Cost breakdown: ${self.node_config.cpu_cost_per_hour:.3f}/core/hour, "
            f"${self.node_config.memory_cost_per_hour:.3f}/GB/hour"
        )
        print("")

        # Get data
        print("📊 Collecting usage data...")
        usage_data = self.k8s_client.get_pod_usage()

        print("📋 Collecting request data...")
        request_data = self.k8s_client.get_pod_requests()

        print("🧮 Calculating waste...")
        print("")

        # Calculate waste
        workload_waste, total_cpu_waste, total_memory_waste, total_cost_waste = (
            self.calculator.calculate(request_data, usage_data)
        )

        # Print report
        self.reporter.print_report(
            workload_waste, total_cpu_waste, total_memory_waste,
            total_cost_waste, len(request_data)
        )


def parse_args():
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        description="Calculate Kubernetes cluster resource waste and costs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 waste_calc.py
  python3 waste_calc.py --cpu-cores 8 --memory-gb 32 --cost-per-hour 0.48
  python3 waste_calc.py --cost-per-hour 0.35
        """
    )

    parser.add_argument(
        "--cpu-cores",
        type=float,
        default=DEFAULT_NODE_CPU_CORES,
        help=f"CPU cores per node (default: {DEFAULT_NODE_CPU_CORES})"
    )

    parser.add_argument(
        "--memory-gb",
        type=float,
        default=DEFAULT_NODE_MEMORY_GB,
        help=f"Memory in GB per node (default: {DEFAULT_NODE_MEMORY_GB})"
    )

    parser.add_argument(
        "--cost-per-hour",
        type=float,
        default=DEFAULT_NODE_COST_PER_HOUR,
        help=f"Cost per node per hour in dollars (default: ${DEFAULT_NODE_COST_PER_HOUR})"
    )

    return parser.parse_args()


def main():
    """Entry point"""
    args = parse_args()
    node_config = NodeConfig(args.cpu_cores, args.memory_gb, args.cost_per_hour)
    analyzer = ClusterWasteAnalyzer(node_config)
    analyzer.analyze()


if __name__ == "__main__":
    main()
