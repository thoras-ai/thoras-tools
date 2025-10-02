# Kubernetes Workload Waste Calculator

Analyzes Kubernetes cluster resource waste by comparing pod resource requests against actual usage. Calculates wasted CPU, memory, and associated costs to help identify optimization opportunities.

## Overview

This tool:
- Queries your Kubernetes cluster for pod resource requests and actual usage
- Calculates waste assuming a 90% utilization target
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
python3 waste_calc.py
```

Specify custom node configuration:

```bash
python3 waste_calc.py --cpu-cores 8 --memory-gb 32 --cost-per-hour 0.48
```

Override just the cost:

```bash
python3 waste_calc.py --cost-per-hour 0.35
```

## Arguments

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `--cpu-cores` | float | 4 | Total CPU cores per node in your cluster |
| `--memory-gb` | float | 17 | Total memory in GB per node in your cluster |
| `--cost-per-hour` | float | 0.192 | Cost per node per hour in dollars |

## How It Works

1. **Collects Data**: Fetches pod resource requests and actual usage from the Kubernetes API
2. **Calculates Waste**: For each pod, determines how much resources are over-allocated relative to a 90% utilization target
3. **Estimates Cost**: Uses node cost and a 65/35 CPU/memory cost split to estimate waste cost
4. **Aggregates**: Groups pods by workload and ranks by cost impact
5. **Reports**: Displays top waste contributors and cluster-wide waste summary

### Waste Calculation

For each resource (CPU/memory):
```
Target allocation = actual usage / 0.9
Waste = current request - target allocation
```

This assumes resources should be allocated to achieve 90% utilization.

### Cost Model

- Assumes 65% of node cost is for CPU, 35% for memory
- Calculates per-core and per-GB hourly cost
- Applies these rates to wasted resources

## Output

The tool provides:
- **Top 10 waste contributors** by workload with CPU, memory, and annual cost waste
- **Cluster summary** with total waste and annual cost impact
- **Efficiency insights** suggesting which workloads to right-size

## Example Output

```
🔍 Calculating cluster waste...
Node specs: 4 cores, 17GB, $0.192/hour
Cost breakdown: $0.031/core/hour, $0.004/GB/hour

📊 Collecting usage data...
📋 Collecting request data...
🧮 Calculating waste...

=== TOP WASTE CONTRIBUTORS (by workload) ===
default/nginx-deployment              CPU:   2.45 cores  Memory:   3.20 GB  Annual waste: $2,156 [3 pods]
kube-system/coredns                   CPU:   0.80 cores  Memory:   1.50 GB  Annual waste: $743 [2 pods]

=== CLUSTER WASTE SUMMARY ===
Pods analyzed: 42
Total CPU waste: 8.32 cores
Total memory waste: 12.45 GB
Hourly cost waste: $0.31
💰 ANNUAL cost waste: $2,716

Efficiency insight: Consider right-sizing the high-waste pods above
✅ Analysis complete!
```
