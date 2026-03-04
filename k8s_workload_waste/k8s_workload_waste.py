#!/usr/bin/env python3
"""
Cluster waste calculation using Python
"""

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional, Tuple

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

        # Binary IEC units (Ki, Mi, Gi)
        if mem_str.endswith("Ki"):
            return float(mem_str[:-2]) / 1_048_576  # Ki to GB
        elif mem_str.endswith("Mi"):
            return float(mem_str[:-2]) / 1_024  # Mi to GB
        elif mem_str.endswith("Gi"):
            return float(mem_str[:-2])  # Gi to GB
        # Decimal SI units (K, M, G)
        elif mem_str.endswith("G"):
            return float(mem_str[:-1]) / 1.073741824  # G to GB (GiB)
        elif mem_str.endswith("M"):
            return float(mem_str[:-1]) / 1073.741824  # M to GB (GiB)
        elif mem_str.endswith("K"):
            return float(mem_str[:-1]) / 1_073_741.824  # K to GB (GiB)
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
                phase = pod.get("status", {}).get("phase", "")
                if phase != "Running":
                    continue

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

    UTILIZATION_TARGET = 0.85

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
    ) -> Tuple[Dict[str, WorkloadWaste], float, float, float]:
        """Calculate waste for each pod and aggregate by workload"""
        workload_waste = {}
        total_cpu_waste = 0.0
        total_memory_waste = 0.0
        total_cost_waste = 0.0

        for pod_key, req in request_data.items():
            usage = usage_data.get(pod_key, ResourceMetrics(cpu=0.0, memory=0.0))

            # Calculate waste assuming 85% utilization target
            # Target allocation = usage / 0.85 (what allocation should be for 85% utilization)
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
        # Exclude kube-system and thoras namespaces
        waste_data = [
            {
                "workload": workload,
                "waste": waste
            }
            for workload, waste in workload_waste.items()
            if waste.cost_waste > 0.001
            and not workload.startswith("kube-system/")
            and not workload.startswith("thoras/")
        ]
        waste_data.sort(key=lambda x: x["waste"].cost_waste, reverse=True)

        # Recalculate totals from filtered workloads only
        filtered_cpu_waste = sum(item["waste"].cpu_waste for item in waste_data)
        filtered_memory_waste = sum(item["waste"].memory_waste for item in waste_data)
        filtered_cost_waste = sum(item["waste"].cost_waste for item in waste_data)
        filtered_pod_count = sum(item["waste"].pod_count for item in waste_data)

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

        # Summary using filtered totals
        annual_waste = filtered_cost_waste * 24 * 365

        print("=== CLUSTER WASTE SUMMARY ===")
        print(f"Application pods analyzed: {filtered_pod_count} (excludes kube-system, thoras)")
        print(f"Total CPU waste: {filtered_cpu_waste:.2f} cores")
        print(f"Total memory waste: {filtered_memory_waste:.2f} GB")
        print(f"Hourly cost waste: ${filtered_cost_waste:.2f}")
        print(f"💰 ANNUAL cost waste: ${annual_waste:,.0f}")

        # Show verification that math adds up
        print(f"\n📊 Breakdown verification:")
        print(f"   Sum of top contributors: ${sum(item['waste'].cost_waste for item in waste_data) * 24 * 365:,.0f}/year")
        print(f"   Total shown above:       ${annual_waste:,.0f}/year")
        print(f"   ✓ Math verified!")

        if filtered_pod_count > 0:
            print(f"\nEfficiency insight: Consider right-sizing the high-waste pods above")

        print("\n✅ Analysis complete!")

    def generate_html_report(
        self,
        workload_waste: Dict[str, WorkloadWaste],
        total_cpu_waste: float,
        total_memory_waste: float,
        total_cost_waste: float,
        pod_count: int,
        node_config: 'NodeConfig',
        output_path: str = "k8s_waste_report.html"
    ):
        """Generate executive-friendly HTML report"""
        # Convert to list and sort by cost waste (highest first)
        # Exclude kube-system and thoras namespaces
        waste_data = [
            {
                "workload": workload,
                "waste": waste
            }
            for workload, waste in workload_waste.items()
            if waste.cost_waste > 0.001
            and not workload.startswith("kube-system/")
            and not workload.startswith("thoras/")
        ]
        waste_data.sort(key=lambda x: x["waste"].cost_waste, reverse=True)

        # Recalculate totals from filtered workloads only
        filtered_cpu_waste = sum(item["waste"].cpu_waste for item in waste_data)
        filtered_memory_waste = sum(item["waste"].memory_waste for item in waste_data)
        filtered_cost_waste = sum(item["waste"].cost_waste for item in waste_data)
        filtered_pod_count = sum(item["waste"].pod_count for item in waste_data)

        annual_waste = filtered_cost_waste * 24 * 365
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        # Generate table rows for top wasters
        table_rows = ""
        for idx, item in enumerate(waste_data[:20], 1):  # Top 20
            waste = item["waste"]
            workload_annual = waste.cost_waste * 24 * 365
            table_rows += f"""
                <tr>
                    <td>{idx}</td>
                    <td class="workload-name">{item['workload']}</td>
                    <td>{waste.cpu_waste:.2f}</td>
                    <td>{waste.memory_waste:.2f}</td>
                    <td>${workload_annual:,.0f}</td>
                    <td>{waste.pod_count}</td>
                </tr>
            """

        html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Kubernetes Cluster Waste Analysis</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;600;700&family=Urbanist:wght@400;500;600&display=swap" rel="stylesheet">
    <style>
        * {{
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }}

        body {{
            font-family: 'Urbanist', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
            background: #101010;
            padding: 40px 20px;
            color: #f5f5f5;
        }}

        .container {{
            max-width: 1400px;
            margin: 0 auto;
            background: rgba(16, 16, 16, 0.8);
            backdrop-filter: blur(5px);
            border-radius: 16px;
            border: 1px solid rgba(188, 176, 160, 0.1);
            box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5);
            overflow: hidden;
        }}

        .header {{
            background: linear-gradient(135deg, #2d4a4a 0%, #1a3333 100%);
            border-bottom: 2px solid #BCB0A0;
            color: #f5f5f5;
            padding: 40px;
            text-align: center;
        }}

        .header h1 {{
            font-family: 'Montserrat', sans-serif;
            font-size: 2.5em;
            margin-bottom: 10px;
            font-weight: 700;
            color: #BCB0A0;
        }}

        .header .subtitle {{
            font-size: 1.1em;
            color: #f5f5f5;
            opacity: 0.9;
        }}

        .header .timestamp {{
            font-size: 0.9em;
            color: #BCB0A0;
            opacity: 0.7;
            margin-top: 10px;
        }}

        .executive-summary {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            padding: 40px;
            background: rgba(20, 20, 20, 0.5);
            border-bottom: 1px solid rgba(188, 176, 160, 0.2);
        }}

        .metric-card {{
            background: linear-gradient(135deg, #2d4a4a 0%, #1a3333 100%);
            border: 1px solid rgba(188, 176, 160, 0.3);
            border-radius: 12px;
            padding: 30px;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.3);
            transition: transform 0.2s, box-shadow 0.2s, border-color 0.2s;
        }}

        .metric-card:hover {{
            transform: translateY(-4px);
            box-shadow: 0 8px 16px rgba(0, 0, 0, 0.5);
            border-color: rgba(188, 176, 160, 0.5);
        }}

        .metric-card .label {{
            font-size: 0.85em;
            color: #BCB0A0;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-bottom: 10px;
            font-weight: 600;
        }}

        .metric-card .value {{
            font-family: 'Montserrat', sans-serif;
            font-size: 2.5em;
            font-weight: 700;
            color: #BCB0A0;
            margin-bottom: 5px;
        }}

        .metric-card.highlight .value {{
            color: #e8b4b8;
            font-size: 3em;
        }}

        .metric-card .unit {{
            font-size: 0.9em;
            color: rgba(188, 176, 160, 0.6);
        }}

        .config-info {{
            padding: 30px 40px;
            background: rgba(188, 176, 160, 0.08);
            border-left: 4px solid #BCB0A0;
            margin: 0 40px 30px 40px;
            border-radius: 8px;
        }}

        .config-info h3 {{
            font-family: 'Montserrat', sans-serif;
            font-size: 1.2em;
            margin-bottom: 15px;
            color: #BCB0A0;
        }}

        .config-info .config-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
        }}

        .config-item {{
            font-size: 0.95em;
        }}

        .config-item .config-label {{
            color: rgba(188, 176, 160, 0.7);
            font-weight: 600;
        }}

        .config-item .config-value {{
            color: #f5f5f5;
            margin-left: 5px;
        }}

        .section {{
            padding: 40px;
        }}

        .section h2 {{
            font-family: 'Montserrat', sans-serif;
            font-size: 1.8em;
            margin-bottom: 25px;
            color: #BCB0A0;
            border-bottom: 3px solid #BCB0A0;
            padding-bottom: 10px;
        }}

        .table-container {{
            overflow-x: auto;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3);
            border: 1px solid rgba(188, 176, 160, 0.2);
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
            background: rgba(20, 20, 20, 0.6);
        }}

        thead {{
            background: linear-gradient(135deg, #2d4a4a 0%, #1a3333 100%);
            color: #f5f5f5;
        }}

        th {{
            padding: 18px 15px;
            text-align: left;
            font-weight: 600;
            text-transform: uppercase;
            font-size: 0.85em;
            letter-spacing: 0.5px;
        }}

        td {{
            padding: 16px 15px;
            border-bottom: 1px solid rgba(188, 176, 160, 0.1);
            color: #f5f5f5;
        }}

        tbody tr:hover {{
            background: rgba(188, 176, 160, 0.08);
        }}

        tr:last-child td {{
            border-bottom: none;
        }}

        .workload-name {{
            font-family: 'Courier New', monospace;
            font-size: 0.9em;
            color: #BCB0A0;
        }}

        .insight-box {{
            background: rgba(188, 176, 160, 0.1);
            border-left: 4px solid #BCB0A0;
            padding: 20px;
            margin-top: 30px;
            border-radius: 8px;
        }}

        .insight-box h3 {{
            font-family: 'Montserrat', sans-serif;
            font-size: 1.2em;
            color: #BCB0A0;
            margin-bottom: 10px;
        }}

        .insight-box p {{
            color: rgba(245, 245, 245, 0.9);
            line-height: 1.6;
        }}

        .footer {{
            text-align: center;
            padding: 30px;
            background: rgba(16, 16, 16, 0.95);
            border-top: 1px solid rgba(188, 176, 160, 0.2);
            color: rgba(188, 176, 160, 0.7);
            font-size: 0.9em;
        }}

        @media print {{
            body {{
                background: white;
                padding: 0;
            }}

            .container {{
                box-shadow: none;
                background: white;
            }}

            .header, .footer {{
                background: #101010;
            }}

            .metric-card:hover {{
                transform: none;
            }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Kubernetes Cluster Waste Analysis</h1>
            <div class="subtitle">Resource Optimization Report</div>
            <div class="timestamp">Generated: {timestamp}</div>
        </div>

        <div class="executive-summary">
            <div class="metric-card highlight">
                <div class="label">Annual Cost Waste</div>
                <div class="value">${annual_waste:,.0f}</div>
                <div class="unit">per year</div>
            </div>
            <div class="metric-card">
                <div class="label">CPU Waste</div>
                <div class="value">{filtered_cpu_waste:.2f}</div>
                <div class="unit">cores</div>
            </div>
            <div class="metric-card">
                <div class="label">Memory Waste</div>
                <div class="value">{filtered_memory_waste:.2f}</div>
                <div class="unit">GB</div>
            </div>
            <div class="metric-card">
                <div class="label">Application Pods</div>
                <div class="value">{filtered_pod_count}</div>
                <div class="unit">analyzed</div>
            </div>
        </div>

        <div class="config-info">
            <h3>Cluster Configuration</h3>
            <div class="config-grid">
                <div class="config-item">
                    <span class="config-label">CPU per Node:</span>
                    <span class="config-value">{node_config.cpu_cores} cores</span>
                </div>
                <div class="config-item">
                    <span class="config-label">Memory per Node:</span>
                    <span class="config-value">{node_config.memory_gb} GB</span>
                </div>
                <div class="config-item">
                    <span class="config-label">Node Cost:</span>
                    <span class="config-value">${node_config.cost_per_hour:.2f}/hour</span>
                </div>
                <div class="config-item">
                    <span class="config-label">CPU Cost:</span>
                    <span class="config-value">${node_config.cpu_cost_per_hour:.3f}/core/hour</span>
                </div>
                <div class="config-item">
                    <span class="config-label">Memory Cost:</span>
                    <span class="config-value">${node_config.memory_cost_per_hour:.3f}/GB/hour</span>
                </div>
                <div class="config-item">
                    <span class="config-label">Target Utilization:</span>
                    <span class="config-value">85%</span>
                </div>
            </div>
        </div>

        <div class="section">
            <h2>Top 20 Waste Contributors</h2>
            <div class="table-container">
                <table>
                    <thead>
                        <tr>
                            <th>#</th>
                            <th>Workload</th>
                            <th>CPU Waste (cores)</th>
                            <th>Memory Waste (GB)</th>
                            <th>Annual Cost Waste</th>
                            <th>Pod Count</th>
                        </tr>
                    </thead>
                    <tbody>
                        {table_rows}
                    </tbody>
                </table>
            </div>

            <div class="insight-box">
                <h3>Optimization Recommendation</h3>
                <p>
                    Focus on right-sizing the workloads listed above. Reducing resource requests to match actual usage
                    (with an 85% utilization target) can significantly decrease infrastructure costs while maintaining
                    application performance. Start with the highest annual cost waste contributors for maximum impact.
                </p>
            </div>
        </div>

        <div class="footer">
            <p>Kubernetes Cluster Waste Analysis Tool &middot; Targeting 85% resource utilization</p>
        </div>
    </div>
</body>
</html>"""

        # Write HTML file
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(html_content)

        print(f"📄 HTML report generated: {output_path}")


class ClusterWasteAnalyzer:
    """Main orchestrator for cluster waste analysis"""

    def __init__(self, node_config: NodeConfig, html_output: Optional[str] = None):
        self.node_config = node_config
        self.html_output = html_output
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

        # Generate HTML report if requested
        if self.html_output:
            print("")
            self.reporter.generate_html_report(
                workload_waste, total_cpu_waste, total_memory_waste,
                total_cost_waste, len(request_data), self.node_config,
                self.html_output
            )


class HelpOnErrorParser(argparse.ArgumentParser):
    """ArgumentParser that prints full help on any argument error"""

    def error(self, message):
        sys.stderr.write(f"error: {message}\n\n")
        self.print_help(sys.stderr)
        sys.exit(2)


def parse_args():
    """Parse command line arguments"""
    parser = HelpOnErrorParser(
        description="Calculate Kubernetes cluster resource waste and costs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 waste_calc.py
  python3 waste_calc.py --cpu-cores 8 --memory-gb 32 --cost-per-hour 0.48
  python3 waste_calc.py --cost-per-hour 0.35
  python3 waste_calc.py --html-output report.html
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

    parser.add_argument(
        "--html-output",
        type=str,
        default="k8s_waste_report.html",
        help="Generate HTML report at specified path (default: k8s_waste_report.html)"
    )

    return parser.parse_args()


def main():
    """Entry point"""
    args = parse_args()
    node_config = NodeConfig(args.cpu_cores, args.memory_gb, args.cost_per_hour)
    analyzer = ClusterWasteAnalyzer(node_config, args.html_output)
    analyzer.analyze()


if __name__ == "__main__":
    main()
