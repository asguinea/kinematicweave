# Data and artifact licensing

## Source code and project-created material

Unless a file states otherwise, KinematicWeave source code, project-created
documentation, configuration, and synthetic fixtures are licensed under
Apache-2.0. See [LICENSE](LICENSE).

The fixtures below were created for this repository and do not contain copied
Argoverse 2 records or geometry:

- `tests/fixtures/av2_motion/scenario_fixture.json`
- `tests/fixtures/av2_map/log_map_archive_fixture-scenario-001.json`

## Argoverse 2

KinematicWeave can process the Argoverse 2 Motion Forecasting Dataset. The
source dataset is not included in this repository. Argoverse 2 data and
documentation are made available under the Creative Commons
Attribution-NonCommercial-ShareAlike 4.0 International license
([CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/)).
The provider's complete terms are available from the
[Argoverse 2 terms of use](https://www.argoverse.org/av2.html).

Required attribution for Argoverse material:

> © 2022 Argo AI, LLC

The aggregate records and visual artifacts under the following paths were
created from Argoverse 2 inputs and are distributed under CC BY-NC-SA 4.0:

- `results/phase2/` through `results/phase5/`, except records explicitly
  marked as synthetic-only;
- `results/benchmark_figures/` and `results/showcase_media/`;
- `figures/benchmark/`, `figures/showcase/`, and `tables/benchmark/`;
- reports whose inputs identify an Argoverse 2 cohort.

The Apache-2.0 license for KinematicWeave code does not replace or relax those
dataset terms. In particular, users are responsible for the non-commercial,
attribution, and share-alike requirements that apply to dataset-derived
material.

Before downloading or processing Argoverse 2, review the provider's current
terms and follow its requested dataset citation guidance.
