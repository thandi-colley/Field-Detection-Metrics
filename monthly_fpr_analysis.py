"""
Monthly FPR Analysis Tool

This script performs comprehensive monthly analysis of FiftyOne intrusion event classifications.

Required inputs:
1. Current month CSV path
2. Previous month CSV path
3. Population statistics (devices, total events) - from Redshift query

Outputs:
- Overall FPR with Wilson confidence intervals
- Month-over-month comparison
- Comprehensive field breakdowns for all non-discard events
- FP breakdown by cause, intermediary class, environment
- Device-level statistics
- Per-device-per-week rates
- TP out-of-bed analysis
- Happy accident details
"""

import csv
import json
import math
import os
import argparse
from collections import Counter, defaultdict
from pathlib import Path


OUTPUT_REPORTS_DIR = Path(__file__).parent / "outputs" / "reports"


def wilson_confidence_interval(successes, trials, confidence=0.95):
    """Calculate Wilson score confidence interval."""
    if trials == 0:
        return 0, 0, 0
    
    p = successes / trials
    z = 1.96 if confidence == 0.95 else 2.576
    
    denominator = 1 + z**2 / trials
    center = (p + z**2 / (2 * trials)) / denominator
    margin = z * math.sqrt((p * (1 - p) + z**2 / (4 * trials)) / trials) / denominator
    
    return p, max(0, center - margin), min(1, center + margin)


def analyze_month(csv_path, month_name):
    """Analyze a single month's CSV data."""
    with open(csv_path, 'r') as f:
        reader = csv.DictReader(f)
        rows = list(reader)
    
    # Filter valid events
    valid = [r for r in rows if not r['discard_criteria']]
    tps = [r for r in valid if r['major_class'] == 'true positive']
    fps = [r for r in valid if r['major_class'] == 'false positive']
    
    # Calculate FPR
    fpr, ci_lower, ci_upper = wilson_confidence_interval(len(fps), len(valid))
    
    results = {
        'month': month_name,
        'total_sample': len(rows),
        'valid_events': len(valid),
        'discards': len(rows) - len(valid),
        'discard_rate': (len(rows) - len(valid)) / len(rows),
        'tps': len(tps),
        'fps': len(fps),
        'fpr': fpr,
        'ci_lower': ci_lower,
        'ci_upper': ci_upper,
        'rows': rows,
        'valid': valid,
        'tps_list': tps,
        'fps_list': fps
    }
    
    return results


def comprehensive_breakdowns(valid_events, all_events):
    """Generate comprehensive statistics for all fields."""
    return {
        'major_class': Counter(r['major_class'] for r in valid_events if r['major_class']),
        'intermediary_class': Counter(r['intermediary_class'] for r in valid_events if r['intermediary_class']),
        'environment': Counter(r['environment'] for r in valid_events if r['environment']),
        'body_position': Counter(r['body_position'] for r in valid_events if r['body_position']),
        'interaction_type': Counter(r['interaction_type'] for r in valid_events if r['interaction_type']),
        'body_part_in_bed': Counter(r['body_part_in_bed'] for r in valid_events if r['body_part_in_bed']),
        'intruding_from': Counter(r['intruding_from'] for r in valid_events if r['intruding_from']),
        'discard_criteria': Counter(r['discard_criteria'] for r in all_events if r['discard_criteria'])
    }


def fp_breakdown(fps):
    """Detailed FP analysis by cause, intermediary, environment."""
    causes = Counter(r['fp_cause'] for r in fps if r['fp_cause'])
    intermediary = Counter(r['intermediary_class'] for r in fps if r['intermediary_class'])
    environments = Counter(r['environment'] for r in fps if r['environment'])
    
    return {
        'causes': causes,
        'intermediary': intermediary,
        'environments': environments
    }


def tp_out_of_bed_analysis(tps):
    """Analyze TP body position and out-of-bed detections."""
    out_of_bed = [r for r in tps if not r['body_part_in_bed']]
    body_parts = Counter(r['body_part_in_bed'] for r in tps)
    body_positions = Counter(r['body_position'] for r in tps)
    
    return {
        'out_of_bed_count': len(out_of_bed),
        'out_of_bed_pct': len(out_of_bed) / len(tps) if tps else 0,
        'body_parts': body_parts,
        'body_positions': body_positions
    }


def calculate_device_rates(valid_events, fps, num_devices_population, total_events_population, weeks):
    """Calculate per-device per-week rates extrapolated to full population."""
    discard_rate = 1 - (len(valid_events) / (len(valid_events) + 100))  # Approximate
    valid_events_population = total_events_population * (1 - discard_rate)
    
    fpr = len(fps) / len(valid_events) if valid_events else 0
    expected_fps_population = valid_events_population * fpr
    expected_tps_population = valid_events_population * (1 - fpr)
    
    return {
        'avg_events_per_device_per_week': valid_events_population / num_devices_population / weeks,
        'avg_tps_per_device_per_week': expected_tps_population / num_devices_population / weeks,
        'avg_fps_per_device_per_week': expected_fps_population / num_devices_population / weeks,
        'expected_valid_events': valid_events_population,
        'expected_tps': expected_tps_population,
        'expected_fps': expected_fps_population
    }


def print_monthly_report(current, previous=None, device_stats=None, output_file=None):
    """Print comprehensive monthly analysis report."""
    
    def _print(*args, **kwargs):
        """Helper to print to both console and file if specified."""
        print(*args, **kwargs)
        if output_file:
            print(*args, **kwargs, file=output_file)
    
    _print("=" * 80)
    _print(f"MONTHLY FPR ANALYSIS: {current['month']}")
    _print("=" * 80)
    _print()
    
    # Overall Statistics
    _print("OVERALL STATISTICS")
    _print("-" * 80)
    _print(f"Sample size: {current['total_sample']:,} events")
    _print(f"Valid events: {current['valid_events']:,} ({current['valid_events']/current['total_sample']*100:.1f}%)")
    _print(f"Discards: {current['discards']:,} ({current['discard_rate']*100:.1f}%)")
    _print(f"True Positives: {current['tps']:,} ({current['tps']/current['valid_events']*100:.2f}%)")
    _print(f"False Positives: {current['fps']:,} ({current['fps']/current['valid_events']*100:.2f}%)")
    _print(f"FPR: {current['fpr']*100:.2f}% [95% CI: {current['ci_lower']*100:.2f}%, {current['ci_upper']*100:.2f}%]")
    _print()
    
    # Comparison with previous month
    if previous:
        _print("COMPARISON WITH PREVIOUS MONTH")
        _print("-" * 80)
        fpr_change = (current['fpr'] - previous['fpr']) * 100
        _print(f"FPR Change: {fpr_change:+.2f}pp ({previous['fpr']*100:.2f}% → {current['fpr']*100:.2f}%)")
        _print(f"Valid Events: {previous['valid_events']:,} → {current['valid_events']:,} ({(current['valid_events']/previous['valid_events']-1)*100:+.1f}%)")
        _print(f"Discard Rate: {previous['discard_rate']*100:.1f}% → {current['discard_rate']*100:.1f}% ({(current['discard_rate']-previous['discard_rate'])*100:+.1f}pp)")
        _print()
    
    # Hierarchical Classification Breakdown (Following Annotation Framework)
    _print("HIERARCHICAL CLASSIFICATION (Following Annotation Framework)")
    _print("=" * 80)
    
    # Level 1: SECURITY EVENT (All events are security events)
    _print("\nSECURITY EVENT (radar detection)")
    _print("  Total events: {:,}".format(len(current['rows'])))
    _print("  Valid events: {:,} ({:.1f}%)".format(len(current['valid']), len(current['valid'])/len(current['rows'])*100))
    _print("  Discards: {:,} ({:.1f}%)".format(current['discards'], current['discard_rate']*100))
    
    # Level 2: PRIMARY CLASS
    _print("\n  PRIMARY CLASS")
    _print("  " + "-" * 76)
    _print("  INTRUSION EVENT (cv detection): {:,} (100%)".format(len(current['valid'])))
    _print("  LEARNING EVENT (no cv detection): 0 (not yet classified)")
    
    # Level 3: MAJOR CLASS (under INTRUSION EVENT)
    _print("\n    MAJOR CLASS")
    _print("    " + "-" * 74)
    tps = [r for r in current['valid'] if r['major_class'] == 'true positive']
    fps = [r for r in current['valid'] if r['major_class'] == 'false positive']
    _print("    TRUE POSITIVE: {:,} ({:.1f}%)".format(len(tps), len(tps)/len(current['valid'])*100))
    _print("    FALSE POSITIVE: {:,} ({:.1f}%)".format(len(fps), len(fps)/len(current['valid'])*100))
    
    # Level 4 & 5: INTERMEDIARY and MINOR CLASS for TRUE POSITIVES
    _print("\n      TRUE POSITIVE > INTERMEDIARY CLASS")
    _print("      " + "-" * 72)
    
    # In-bed detections
    in_bed = [r for r in tps if r['intermediary_class'] == 'in-bed detection']
    _print("      IN-BED DETECTION: {:,} ({:.1f}%)".format(len(in_bed), len(in_bed)/len(tps)*100 if tps else 0))
    if in_bed:
        body_parts = Counter(r['body_part_in_bed'] for r in in_bed if r['body_part_in_bed'])
        intruding_from = Counter(r['intruding_from'] for r in in_bed if r['intruding_from'])
        for part, count in body_parts.most_common():
            _print("        └─ {}: {}".format(part, count))
        _print("        Intruding from:")
        for loc, count in intruding_from.most_common():
            _print("          └─ {}: {}".format(loc, count))
    
    # Out-of-bed detections
    out_bed = [r for r in tps if r['intermediary_class'] == 'out-of-bed detection']
    _print("\n      OUT-OF-BED DETECTION: {:,} ({:.1f}%)".format(len(out_bed), len(out_bed)/len(tps)*100 if tps else 0))
    if out_bed:
        positions = Counter(r['body_position'] for r in out_bed if r['body_position'])
        for pos, count in positions.most_common():
            _print("        └─ {}: {}".format(pos, count))
    
    # Level 4 & 5: INTERMEDIARY and MINOR CLASS for FALSE POSITIVES
    _print("\n      FALSE POSITIVE > INTERMEDIARY CLASS")
    _print("      " + "-" * 72)
    
    # Ghost
    ghost = [r for r in fps if r['intermediary_class'] == 'ghost']
    _print("      GHOST: {:,} ({:.1f}%)".format(len(ghost), len(ghost)/len(fps)*100 if fps else 0))
    if ghost:
        causes = Counter(r['fp_cause'] for r in ghost if r['fp_cause'])
        for cause, count in causes.most_common():
            _print("        └─ {}: {}".format(cause, count))
    
    # Happy Accident
    happy = [r for r in fps if r['intermediary_class'] == 'happy accident']
    _print("\n      HAPPY ACCIDENT: {:,} ({:.1f}%)".format(len(happy), len(happy)/len(fps)*100 if fps else 0))
    if happy:
        causes = Counter(r['fp_cause'] for r in happy if r['fp_cause'])
        for cause, count in causes.most_common():
            _print("        └─ {}: {}".format(cause, count))
    
    # Environment breakdown (applies to all events)
    _print("\n  ENVIRONMENT (All Valid Events)")
    _print("  " + "-" * 76)
    breakdowns = comprehensive_breakdowns(current['valid'], current['rows'])
    for env, count in breakdowns['environment'].most_common()[:10]:
        pct = count / len(current['valid']) * 100
        _print("    {}: {:,} ({:.1f}%)".format(env, count, pct))
    
    # Discard Criteria
    _print("\n  DISCARD CRITERIA (Excluded Events)")
    _print("  " + "-" * 76)
    for criteria, count in breakdowns['discard_criteria'].most_common():
        pct = count / current['discards'] * 100 if current['discards'] > 0 else 0
        _print("    {}: {:,} ({:.1f}%)".format(criteria, count, pct))
    _print()
    
    # FP Breakdown
    _print("FALSE POSITIVE BREAKDOWN")
    _print("-" * 80)
    fp_analysis = fp_breakdown(current['fps_list'])
    
    _print("\nBy Cause:")
    for cause, count in fp_analysis['causes'].most_common():
        pct = count / len(current['fps_list']) * 100
        _print(f"  {cause:40s} {count:3d} ({pct:5.1f}%)")
    
    _print("\nBy Intermediary Class:")
    for cls, count in fp_analysis['intermediary'].most_common():
        pct = count / len(current['fps_list']) * 100
        _print(f"  {cls:40s} {count:3d} ({pct:5.1f}%)")
    
    _print("\nBy Environment:")
    for env, count in fp_analysis['environments'].most_common():
        pct = count / len(current['fps_list']) * 100
        _print(f"  {env:40s} {count:3d} ({pct:5.1f}%)")
    _print()
    
    # TP Out-of-Bed Analysis
    _print("TRUE POSITIVE ANALYSIS")
    _print("-" * 80)
    tp_analysis = tp_out_of_bed_analysis(current['tps_list'])
    _print(f"Out-of-bed detections: {tp_analysis['out_of_bed_count']:,} ({tp_analysis['out_of_bed_pct']*100:.1f}%)")
    _print(f"In-bed interactions: {len(current['tps_list']) - tp_analysis['out_of_bed_count']:,} ({(1-tp_analysis['out_of_bed_pct'])*100:.1f}%)")
    _print()
    
    # Device-level statistics
    if device_stats:
        _print("DEVICE-LEVEL STATISTICS")
        _print("-" * 80)
        _print(f"Total devices with events: {device_stats['num_devices']}")
        _print(f"Devices with 0 FPs: {device_stats['devices_zero_fps']} ({device_stats['devices_zero_fps']/device_stats['num_devices']*100:.1f}%)")
        _print(f"Devices with ≥1 FP: {device_stats['devices_with_fps']} ({device_stats['devices_with_fps']/device_stats['num_devices']*100:.1f}%)")
        _print()
        
        _print("AVERAGE RATES PER DEVICE PER WEEK")
        _print("-" * 80)
        rates = calculate_device_rates(
            current['valid'], 
            current['fps_list'],
            device_stats['num_devices'],
            device_stats['total_events_population'],
            device_stats['weeks']
        )
        _print(f"Average valid events: {rates['avg_events_per_device_per_week']:.2f}")
        _print(f"Average TPs: {rates['avg_tps_per_device_per_week']:.2f}")
        _print(f"Average FPs: {rates['avg_fps_per_device_per_week']:.2f}")
        _print()
    
    # Happy Accidents
    happy_accidents = [r for r in current['fps_list'] if r['intermediary_class'] == 'happy accident']
    if happy_accidents:
        _print("HAPPY ACCIDENTS")
        _print("-" * 80)
        _print(f"Total: {len(happy_accidents)}")
        for i, event in enumerate(happy_accidents, 1):
            _print(f"\n  #{i} - {event['event_id']}")
            _print(f"      Cause: {event['fp_cause']}")
            _print(f"      Environment: {event['environment'] or '(empty)'}")
            _print(f"      Intruding from: {event['intruding_from']}")
            _print(f"      Body part: {event['body_part_in_bed']}")
        _print()


# Example usage function
def run_monthly_analysis(current_csv, previous_csv=None, 
                        num_devices=None, total_events=None, weeks=4,
                        devices_with_fps=None, output_txt=None):
    """
    Run complete monthly analysis.
    
    Args:
        current_csv: Path to current month's CSV
        previous_csv: Path to previous month's CSV (optional)
        num_devices: Number of devices in population (from Redshift)
        total_events: Total events in population (from Redshift)
        weeks: Number of weeks in the period (typically 4)
        devices_with_fps: Number of devices with at least 1 FP (from Redshift)
        output_txt: Path to output text file (optional, defaults to outputs/reports/monthly_fpr_report.txt)
    """
    
    # Analyze current month
    current = analyze_month(current_csv, "Current Month")
    
    # Analyze previous month if provided
    previous = None
    if previous_csv:
        previous = analyze_month(previous_csv, "Previous Month")
    
    # Prepare device statistics if provided
    device_stats = None
    if num_devices and total_events:
        device_stats = {
            'num_devices': num_devices,
            'total_events_population': total_events,
            'weeks': weeks,
            'devices_with_fps': devices_with_fps or 0,
            'devices_zero_fps': (num_devices - devices_with_fps) if devices_with_fps else None
        }
    
    # Generate report to file
    if output_txt is None:
        OUTPUT_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        output_txt = OUTPUT_REPORTS_DIR / 'monthly_fpr_report.txt'
    else:
        output_txt = Path(output_txt)
        output_txt.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_txt, 'w') as f:
        print_monthly_report(current, previous, device_stats, output_file=f)
    
    print(f"\nReport written to: {output_txt}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Monthly FPR Analysis")
    parser.add_argument("--current", required=True,
                        help="Path to current month classification CSV")
    parser.add_argument("--previous",
                        help="Path to previous month classification CSV (optional)")
    parser.add_argument("--sample-json",
                        help="Path to sample JSON produced by run_sample.py (provides population metadata)")
    parser.add_argument("--num-devices", type=int,
                        help="Population device count (overrides sample JSON)")
    parser.add_argument("--total-events", type=int,
                        help="Population event count (overrides sample JSON)")
    parser.add_argument("--devices-with-fps", type=int,
                        help="Number of devices with at least one FP")
    parser.add_argument("--weeks", type=int, default=4,
                        help="Number of weeks in the analysis period (default: 4)")
    parser.add_argument("--output",
                        help="Output text file path (default: derived from --current CSV name)")
    args = parser.parse_args()

    # Resolve population metadata from sample JSON if provided
    num_devices = args.num_devices
    total_events = args.total_events
    if args.sample_json and os.path.exists(args.sample_json):
        with open(args.sample_json) as f:
            sample_data = json.load(f)
        meta = sample_data.get("metadata", {})
        if num_devices is None:
            num_devices = meta.get("num_devices_population")
        if total_events is None:
            total_events = meta.get("total_events_population")

    # Derive output path from CSV name if not supplied
    output_txt = args.output
    if output_txt is None:
        base = os.path.splitext(os.path.basename(args.current))[0]
        OUTPUT_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        output_txt = OUTPUT_REPORTS_DIR / f"{base}_fpr_report.txt"

    run_monthly_analysis(
        current_csv=args.current,
        previous_csv=args.previous,
        num_devices=num_devices,
        total_events=total_events,
        weeks=args.weeks,
        devices_with_fps=args.devices_with_fps,
        output_txt=output_txt,
    )
