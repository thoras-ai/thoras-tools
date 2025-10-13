# Kubernetes Workload Waste Calculator

Analyzes Kubernetes cluster resource waste by comparing pod resource requests against actual usage. Calculates wasted CPU, memory, and associated costs to help identify optimization opportunities.

## Overview

This tool:
- Queries your Kubernetes cluster for pod resource requests and actual usage
- Calculates waste assuming an 85% utilization target
- Aggregates waste by workload (deployment, statefulset, etc.)
- Estimates hourly and annual cost waste
- Identifies top waste contributors

## Prerequisites

- Python 3
- `kubectl` configured to access your cluster
- Kubernetes Metrics Server running in your cluster

## Usage

Basic usage with default node configuration:

```bash
python3 k8s_workload_waste.py
```

This automatically generates an HTML report at `k8s_waste_report.html` in the current directory.

Specify custom node configuration:

```bash
python3 k8s_workload_waste.py --cpu-cores 8 --memory-gb 32 --cost-per-hour 0.48
```

Override just the cost:

```bash
python3 k8s_workload_waste.py --cost-per-hour 0.35
```

Specify a custom HTML output path:

```bash
python3 k8s_workload_waste.py --html-output custom_report.html
```

## Arguments

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--cpu-cores` | float | 4 | Total CPU cores per node in your cluster |
| `--memory-gb` | float | 17 | Total memory in GB per node in your cluster |
| `--cost-per-hour` | float | 0.192 | Cost per node per hour in dollars |
| `--html-output` | string | k8s_waste_report.html | Path to generate an executive-friendly HTML report |

## How It Works

1. **Collects Data**: Fetches pod resource requests and actual usage from the Kubernetes API
2. **Calculates Waste**: For each pod, determines how much resources are over-allocated relative to an 85% utilization target
3. **Estimates Cost**: Uses node cost and a 65/35 CPU/memory cost split to estimate waste cost
4. **Aggregates**: Groups pods by workload and ranks by cost impact
5. **Reports**: Displays top waste contributors and cluster-wide waste summary

### Waste Calculation

For each resource (CPU/memory):
```
Target allocation = actual usage / 0.85
Waste = current request - target allocation
```

This assumes resources should be allocated to achieve 85% utilization.

### Cost Model

- Assumes 65% of node cost is for CPU, 35% for memory
- Calculates per-core and per-GB hourly cost
- Applies these rates to wasted resources

## Output

The tool provides:
- **Console output** with top 10 waste contributors, cluster summary, and efficiency insights
- **HTML report** (`k8s_waste_report.html`) automatically generated with detailed analysis

### Console Output

The terminal displays:
- Top 10 waste contributors by workload with CPU, memory, and annual cost waste
- Cluster summary with total waste and annual cost impact
- Efficiency insights suggesting which workloads to right-size

### HTML Report

An HTML report is automatically generated at `k8s_waste_report.html` (customizable with `--html-output`) featuring:
- **Visual metrics dashboard** highlighting annual cost waste with responsive cards
- **Cluster configuration details** showing node specs and cost breakdown
- **Top 20 waste contributors table** with detailed resource waste metrics
- **Professional design** with modern gradients, hover effects, and print-optimized styling
- **Optimization recommendations** for reducing infrastructure costs

The HTML report is ideal for sharing with stakeholders and executives.

## Example Console Output

```
🔍 Calculating cluster waste...
Node specs: 4 cores, 17GB, $0.192/hour
Cost breakdown: $0.031/core/hour, $0.004/GB/hour

📊 Collecting usage data...
📋 Collecting request data...
🧮 Calculating waste...

=== TOP WASTE CONTRIBUTORS (by workload) ===
default/nginx-deployment              CPU:   2.45 cores  Memory:   3.20 GB  Annual waste: $2,156 [3 pods]
default/api-server                    CPU:   1.80 cores  Memory:   2.10 GB  Annual waste: $1,543 [5 pods]

=== CLUSTER WASTE SUMMARY ===
Application pods analyzed: 42 (excludes kube-system, thoras)
Total CPU waste: 8.32 cores
Total memory waste: 12.45 GB
Hourly cost waste: $0.31
💰 ANNUAL cost waste: $2,716

📊 Breakdown verification:
   Sum of top contributors: $2,716/year
   Total shown above:       $2,716/year
   ✓ Math verified!

Efficiency insight: Consider right-sizing the high-waste pods above

✅ Analysis complete!
📄 HTML report generated: k8s_waste_report.html
```

## Important Notes

- **Namespace filtering**: The tool excludes `kube-system` and `thoras` namespaces from waste calculations to focus on application workloads
- **Metrics Server required**: Ensure the Kubernetes Metrics Server is installed and running in your cluster
- **Cost model**: Uses a 65/35 split for CPU/memory cost allocation
- **Utilization target**: Assumes 85% utilization as the optimal target for resource allocation
