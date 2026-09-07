# Scope and Non-Goals

**Project:** KinematicWeave
**Phase:** 0 — Research and System Definition  
**Batch:** 0.1 — Project Charter and Research Scope  
**Status:** Draft for approval

## 1. Scope policy

The project follows a strict core-versus-optional structure.

- **Core scope** is required to support the primary empirical claims.
- **Secondary scope** is useful but may be simplified when resource or schedule pressure appears.
- **Optional scope** may begin only after the core evidence is stable.
- **Non-goals** are deliberately excluded from the initial research artifact.

This policy protects the evaluation from being delayed by application features or unrelated perception problems.

## 2. Core scope

### 2.1 Scene domain

The core evaluation focuses on structured urban or campus-like environments containing moving agents and persistent path infrastructure.

Expected dynamic entities include:

- vehicles;
- cyclists or similar road users when available;
- pedestrians when adequate trajectory data are available.

Expected persistent structures include:

- lanes or travel corridors;
- pedestrian paths;
- junctions and intersections;
- entrances and exits;
- access or endpoint zones;
- connectivity between traversable elements.

### 2.2 Spatial dimensionality

The primary evaluation is **2.5D**:

- horizontal geometry is represented explicitly;
- heading and time are explicit;
- elevation may be stored or visualized when available;
- primary geometric and topological evaluation occurs in a locally planar frame unless a later definition specifies otherwise.

The system should avoid architectural decisions that prevent a future full-3D extension, but the initial evidence does not require general 3D volumetric inference.

### 2.3 Dynamic representation

The core system includes:

- time-indexed agent trajectories;
- procedural segmentation;
- control points or equivalent symbolic parameters;
- interpolation metadata;
- selected semantic motion events;
- deterministic decoding;
- serialized storage accounting.

### 2.4 Persistent layout representation

The core system includes:

- geometric layout elements;
- a connectivity graph;
- a symbolic or grammar-oriented hierarchy;
- provenance indicating whether an element is ground truth, oracle-converted, inferred, or edited.

### 2.5 Motion-derived layout inference

The core inference task uses repeated trajectories to estimate selected persistent infrastructure.

The evaluation must keep inference inputs separate from evaluation targets. A ground-truth vector map may be used for scoring and oracle representation tests, but not as a hidden input to the trajectory-only inference method.

### 2.6 Runtime operations

The core runtime supports:

- deterministic replay;
- time-based state reconstruction;
- agent and semantic filtering;
- spatial and temporal queries defined in later batches;
- branch creation;
- delay edits;
- trajectory-segment replacement;
- corridor closure;
- supported graph-based rerouting;
- edit provenance.

### 2.7 Evaluation

The core evaluation includes:

- synthetic correctness tests;
- a real-data development subset;
- a frozen real-data evaluation subset;
- fair codec baselines;
- fair layout-inference baselines;
- ablations;
- robustness tests;
- statistical uncertainty;
- resource measurement;
- qualitative success and failure examples.

### 2.8 Engineering stack

The intended core stack is Python-first.

C++ or CUDA may be introduced only when:

- profiling identifies a material bottleneck;
- the optimization preserves the public Python interface;
- correctness remains covered by equivalent tests;
- the dependency and build cost fit the laptop and reproducibility requirements.

## 3. Secondary scope

Secondary work may be implemented when it strengthens the evidence without threatening the critical path.

Examples include:

- spline-based procedural segments;
- additional semantic event types;
- city-held-out evaluation;
- additional graph-comparison metrics;
- interactive visual inspection;
- larger-scale stress tests;
- limited use of GPU acceleration for batched numeric operations;
- additional trajectory datasets with compatible licensing and manageable size.

Secondary scope must not redefine primary metrics after the frozen evaluation begins.

## 4. Optional scope

Optional work begins only after the primary evidence package is complete.

### 4.1 Video-to-tape demonstration

A limited demonstration may use:

- pretrained object detection;
- pretrained tracking;
- provided or estimated depth;
- short video sequences;
- a small annotated driving dataset subset.

This demonstration is not part of the primary proof of the symbolic representation.

### 4.2 XR or game-engine integration

A later demonstration may export the tape to:

- Unreal Engine;
- Unity;
- another interactive 3D environment.

This is not required for the main evaluation.

### 4.3 Native runtime

A C++ runtime may be built later for production-style playback or engine integration. It is not required before the Python runtime has been profiled.

### 4.4 User study

A user study may be considered after the editing interface and tasks are stable. A small convenience sample will not be used as the sole evidence for usability claims.

## 5. Explicit non-goals

### 5.1 Photorealistic reconstruction

The project does not aim to reconstruct photorealistic appearance.

It will not use image fidelity as a primary success criterion and will not claim superiority over NeRF, Gaussian splatting, or video-generation methods on appearance tasks.

### 5.2 Large neural model training

The core project will not require:

- training large object detectors;
- training depth networks;
- training neural radiance fields;
- training large trajectory models;
- distributed training;
- cloud GPU instances.

Pretrained models may be used only in optional demonstrations.

### 5.3 End-to-end perception as the main contribution

Errors in detection, tracking, calibration, depth, or pose estimation must not obscure the main representation experiments.

The primary evaluation starts from trajectories and maps that allow the representation and inference claims to be tested directly.

### 5.4 Arbitrary full-3D reconstruction

The initial project does not infer general building geometry, indoor volumetric structure, deformable surfaces, or arbitrary 3D topology from motion.

### 5.5 Behavioral prediction

The project replays, edits, and reroutes represented behavior. It does not initially claim to predict realistic future behavior after arbitrary interventions.

A rerouted trajectory is an implemented what-if operation, not proof of a validated human or traffic behavior model.

### 5.6 Safety-critical deployment

The repository is not intended for:

- autonomous-vehicle control;
- collision-avoidance certification;
- emergency-management certification;
- infrastructure safety decisions;
- surveillance deployment.

### 5.7 Universal map reconstruction

Motion-derived layout inference will be evaluated only under documented coverage, scene, and annotation conditions.

The project will not imply that any road or path network can be reconstructed from sparse motion.

### 5.8 Production-grade editor

The qualitative viewer may support inspection and scripted edits, but a polished multi-user authoring application is outside the critical path.

### 5.9 Massive-scale digital twin

The project does not target city-wide, continuously updating production twins. It evaluates representative scenario and tile scales that fit the reference laptop.

### 5.10 Dataset creation at sensor scale

The project will not require the complete raw sensor versions of large autonomous-driving datasets when motion and vector-map subsets are sufficient.

## 6. Claim restrictions

### 6.1 Use of the word "3D"

The project name remains KinematicWeave, but reports must distinguish:

- the general 3D concept;
- the dimensions represented by the implementation;
- the dimensions evaluated quantitatively.

For the primary evaluation, preferred language includes:

- "2.5D urban motion and layout";
- "locally planar trajectory and topology evaluation";
- "3D-compatible symbolic representation with 2.5D primary evidence."

Avoid language suggesting arbitrary volumetric reconstruction.

### 6.2 Use of the word "semantic"

A result is semantic only when it refers to a documented category, event, relation, or query.

Compression alone is not semantic. A human-readable visualization alone is not semantic.

### 6.3 Use of the word "deterministic"

Replay is deterministic only under the documented software, numeric, ordering, and hashing conditions.

Approximate visual similarity is not determinism.

### 6.4 Use of the word "compact"

Compactness claims must use a declared serialized-size method that includes all information required to reconstruct the represented output.

Python in-memory object size is not the primary representation-size metric.

### 6.5 Use of the word "editability"

The project may claim implemented symbolic edit operations and measured edit locality.

It must not claim general usability or authoring superiority without an appropriate user evaluation.

### 6.6 Use of the word "inference"

Oracle conversion from a ground-truth map tests representation capacity, not inference.

Trajectory-only estimation is the relevant inference condition.

## 7. Dataset boundaries

The primary dataset selection and split policy will be fixed in a later Phase 0 batch.

Until then:

- no test subset is considered frozen;
- no reported pilot result is a final result;
- dataset-specific assumptions must remain inside adapters;
- large downloads require explicit justification;
- dataset licensing and redistribution rules must be documented;
- no raw or derived restricted data may be committed improperly.

## 8. Resource boundaries

The project must fit within the reference laptop.

The detailed budget will be defined later, but the following boundaries already apply:

- avoid loading complete datasets into memory;
- avoid unbounded pairwise distance matrices;
- stream or partition geographic data;
- cache only necessary processed forms;
- record disk usage for generated artifacts;
- make long experiments resumable;
- avoid mandatory GPU operations;
- assume an 8 GB-class VRAM constraint;
- preserve enough SSD headroom for the operating system and development tools.

## 9. Repository boundaries

The repository should contain:

- source code;
- tests;
- definitions;
- configurations;
- small fixtures;
- scripts;
- documentation;
- result manifests;
- aggregated results where licensing and size permit;
- generated release artifacts where practical.

The repository should not blindly contain:

- full external datasets;
- large disposable caches;
- untracked model weights;
- machine-specific environments;
- secrets;
- manually edited result tables that cannot be regenerated;
- opaque binary outputs without provenance.

## 10. Scope-change rule

A proposed addition is a scope change when it:

- introduces a new primary claim;
- changes the experimental unit;
- changes the primary dataset after freezing;
- changes a primary metric after freezing;
- adds a mandatory heavyweight dependency;
- requires cloud or external compute;
- changes the dimensionality of the primary evaluation;
- delays the core evidence path.

Scope changes require explicit approval and a recorded decision.

## 11. Batch 0.1 acceptance questions

Before approving this document, answer yes to all of the following:

- Is the primary research domain clear?
- Is 2.5D evidence clearly separated from general full-3D ambition?
- Is the core representation isolated from upstream perception?
- Are photorealism and large-model training excluded from the critical path?
- Are replay, query, edit, and layout-inference responsibilities clear?
- Are optional demonstrations prevented from blocking the evaluation?
- Are laptop constraints treated as mandatory?
- Are prohibited claims explicit?
