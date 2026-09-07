# ASUS Laptop Runbook

This runbook targets the reference laptop:

- ASUS ROG Zephyrus G14 GA403UP;
- 32 GB RAM;
- 1 TB SSD;
- NVIDIA RTX 5070 Laptop GPU;
- Ryzen 9-class CPU.

The limits below are approved operating targets, ceilings, and recommendations.
They are not measured benchmark results for this repository or laptop.

## Environment Position

Native Windows is validated for Phase 1 foundation commands and tests. Ubuntu
under WSL2 is the preferred environment for approved scientific work beyond
the foundation. Initial Windows, WSL, driver, firmware, and optional CUDA setup
should follow current official vendor documentation.

The project core must remain CPU-capable. GPU availability is optional during
Phase 1, and a missing GPU may appear as a doctor warning rather than a
foundation failure.

## RAM Limits

- Normal processing target: below 24 GB peak resident memory.
- Warning region: around 22 GB RSS.
- Long-running work should stop or reduce concurrency before approximately
  26 GB RSS.
- Ordinary tests should remain below 4 GB RSS.
- Reduced smoke work should remain below 8 GB RSS.

Do not rely on swapping to complete core work. Use bounded worker counts and
reduce workers, batches, or in-memory scope before approaching the stop region.

## GPU and VRAM Limits

- Normal optional GPU target: below approximately 6.5 GB VRAM.
- Mandatory GPU operations must remain below approximately 7.5 GB VRAM.
- Work exceeding the mandatory ceiling must reduce its batch size, fall back to
  CPU, or remain optional.

The core plan excludes custom CUDA kernels, multi-GPU execution, large-model
training, and GPU-only serialization or replay. No full raw-sensor dataset is
part of the core plan.

## SSD Limits

Maintain at least 15% free SSD space during normal operation. Check capacity
before large approved work and preserve room for the operating system,
development tools, filesystem overhead, and atomic temporary files.

Keep source data, processed data, caches, results, figures, and reports within
their approved ownership directories. Caches may be removed only when their
ownership is known and the operation cannot affect source data or immutable
results.

## Power and Cooling

For sustained work:

- connect AC power;
- keep ventilation unobstructed;
- avoid closed-lid use when it restricts cooling;
- select a stable performance profile;
- record the selected profile when comparing performance;
- avoid comparing benchmarks under materially different power modes.

No unsafe firmware, voltage, thermal, or power modification is required or
recommended.

## Concurrency and Runtime

Use bounded worker counts. Increase concurrency only when observed RAM, disk
I/O, and thermal behavior remain within the approved limits. Reduce workers
before memory or I/O contention compromises stability.

Any later approved long experiment must be split into stable units, preserve
completed work, expose run status, and be resumable after interruption. A
mandatory uninterrupted job should not normally exceed 12 hours.

## WSL2 Placement

When operating inside WSL2:

- prefer Linux-filesystem locations for project source and heavy-I/O data;
- avoid unnecessary cross-filesystem I/O between Windows and WSL;
- keep manifest paths repository-relative;
- document available memory and swap configuration for substantial work;
- validate optional GPU access before relying on it.

Do not assume that native Windows and WSL filesystem performance are
interchangeable.

## Before a Sustained Run

1. Confirm AC power and unobstructed ventilation.
2. Select and record a stable performance profile.
3. Confirm at least 15% free SSD space.
4. Estimate RAM, VRAM, output size, duration, and worker count.
5. Confirm that CPU fallback exists for core work.
6. Confirm that interruption preserves completed units.
7. Run the foundation doctor and local CI checks described in
   [Foundation Setup](foundation_setup.md).

For diagnosis without unsafe system changes, use
[Troubleshooting](troubleshooting.md).
