# CryoPal User Tutorial

## 1. What CryoPal is

CryoPal is a desktop application for organizing and processing cryo-electron tomography datasets in a project-centered way. It is designed to help users keep dataset metadata, processing paths, job history, tomogram thumbnails, particle-analysis outputs, and reusable settings in one place.

CryoPal does not replace the underlying processing software. Instead, it helps you prepare, launch, track, and document jobs for tools such as Warp/WarpTools, MTools/MCore, and other cryo-ET utilities. It also provides quality-control and bookkeeping features that become especially useful when working with many datasets and many tilt series.

This tutorial is written for end users who want to process cryo-ET datasets efficiently and reproducibly.

## 2. Who this guide is for

This document is mainly intended for users who:

- process cryo-ET datasets with Warp/WarpTools and related tools
- want to keep file paths, processing outputs, and metadata organized
- need to run jobs locally or on a Slurm cluster
- want to inspect tomograms and curate them before downstream analysis
- work with particle STAR files and M populations

It is less focused on software development and internal implementation details.

## 3. Core ideas in CryoPal

Before using CryoPal, it helps to understand the core concepts it is built around.

### 3.1 Projects

A CryoPal project is the central container for your work. A project stores:

- the project name
- the list of datasets
- saved paths to raw and processed data
- file-registry rules
- job histories
- thumbnail metadata such as ratings and tags
- M-population information
- particle-analysis settings and optional saved plots
- Slurm profiles, local environments, custom jobs, shortcuts, viewer defaults, and preferences

Projects are saved as `.cryopal.json` files.

### 3.2 Datasets

A dataset is the main experimental unit in CryoPal. Each dataset can store:

- dataset name
- sample name
- comments
- raw-frames directory
- MDOC directory
- optional gain file
- processing directory
- pixel size
- exposure
- tomogram dimensions
- Warp settings files and processing subfolders
- tilt-series names
- job history entries

The same dataset information is reused across Processing, Tomogram Gallery, TS jobs, and Particle jobs.

### 3.3 Centralized path knowledge

CryoPal keeps track of many paths internally. This includes raw data, MDOCs, settings files, tomograms, thumbnails, aligned stacks, tomostars, and custom TS-associated files. The advantage is that once a dataset is set up correctly, many later steps can resolve paths automatically instead of asking you for them again.

### 3.4 Job-oriented workflow

Most processing actions in CryoPal are expressed as jobs. A job can be:

- previewed
- run locally
- scheduled
- submitted to Slurm
- reviewed later in job history

Each processing tab keeps its own history so that WARP, M, TS, particle, and custom jobs stay logically separated.

## 4. Typical end-to-end workflow

For many users, the most natural CryoPal workflow looks like this:

1. Create or open a CryoPal project.
2. Add one or more datasets in `Project Overview`.
3. Verify file roles and path resolution in `File registry`.
4. Run WARP processing jobs in `Processing: WARP`.
5. Inspect reconstructed tomograms in `Tomogram Gallery`.
6. Build and run specialized TS jobs in `Processing: TS jobs`.
7. Create or import M populations in `Processing: M`.
8. Run particle-related jobs in `Processing: Particle jobs`.
9. Use `Check paths`, job history export, and file-path export for documentation and QC.

Not every project uses every tab, but this is a good mental model.

## 5. Starting CryoPal

Launch CryoPal with:

```bash
python3 CryoPal_tomo.py
```

At startup, CryoPal shows a splash screen while the interface is being prepared. When closing, a corresponding closing splash is shown.

## 6. Creating and opening projects

Use the `File` menu for project-level actions:

- `New Project`
- `Open Project...`
- `Open Recent`
- `Save`
- `Save As...`

Important note:

- Saving is disabled while Debug mode is active.

## 7. Project Overview

`Project Overview` is where most users begin.

### 7.1 What you can do here

In this tab, you can:

- name the project
- add new datasets for processing
- import already processed datasets
- remove datasets from CryoPal
- inspect the dataset table
- open dataset details by double-clicking a table entry

### 7.2 Adding a new dataset

When you choose `Dataset actions > Add dataset for processing`, CryoPal lets you define:

- dataset name
- sample
- comment
- raw frames folder
- MDOCs folder
- optional gain file
- processing folder
- pixel size
- exposure
- tomogram dimensions

There are also MDOC-related options such as:

- `Unify mdoc names`
- ignoring `override.mdoc`
- ignoring `custom.mdoc`

Use this mode when you want CryoPal to know your raw input structure and help you drive processing from there.

### 7.3 Importing an already processed dataset

Use `Dataset actions > Import already processed dataset` when a dataset has already been processed outside CryoPal or in another project context and you want CryoPal to adopt its metadata and paths.

This is useful when:

- Warp settings already exist
- tomograms already exist
- thumbnails already exist
- you want to continue work without re-entering all paths manually

### 7.4 Removing a dataset

Use `Dataset actions > Remove Dataset` with care.

Removing a dataset from CryoPal does not delete the raw or processed data itself. However, it removes the dataset from the CryoPal project and discards CryoPal-managed information linked to it, such as:

- tomogram-gallery annotations
- job histories
- associated file-registry associations
- related M-source references where applicable

## 8. File Registry

The `File registry` tab defines how CryoPal resolves shared file roles.

### 8.1 Why this matters

Different tools and tabs need access to files such as:

- tomograms
- aligned stacks
- angle files
- tomostar files
- MDOCs
- thumbnails
- custom TS-matched files such as segmentations or masks

Instead of each tab having its own search logic, CryoPal uses the file registry as a shared source of truth.

### 8.2 Main concepts

For each file role, you can define:

- a title
- a description
- a base directory template
- a filename pattern
- exclude patterns
- whether search is recursive
- whether TS matching should be applied
- how ambiguous matches should be resolved

### 8.3 TS matching

If `Apply TS matching` is enabled, CryoPal tries to associate files with individual tilt-series names. This makes the files usable in places such as:

- Tomogram Gallery details
- TS jobs
- custom job parameter inputs
- file-path export

This is especially powerful for custom file roles like segmentations, masks, or annotation products.

### 8.4 Overrides

You can browse an override for a selected tilt series when automatic resolution is not sufficient. This is useful for edge cases and heterogeneous file layouts.

## 9. Tomogram Gallery

The `Tomogram Gallery` tab is for visual curation and tomogram-centered navigation.

### 9.1 What the gallery provides

You can:

- browse thumbnails
- filter by dataset
- filter by minimum rating
- filter by tag combinations
- inspect the selected tomogram
- open the associated `.mrc`
- link an `.mrc` manually if needed
- add a selected TS to the TS-processing list
- rate a tomogram
- add or remove tags
- delete TS data when needed

### 9.2 Tag filtering

The gallery supports cumulative tag filters. You can define:

- tags that must be included
- whether all or any selected include-tags are required
- tags that must be excluded

This makes it possible to express filters such as:

- include `good_alignment` and `interesting_structure`
- exclude `ice_contamination`

### 9.3 Opening associated files

CryoPal can open associated files using:

- system defaults
- custom viewer exceptions configured in `Settings > Configure viewer defaults`

This is useful for tomograms such as `.mrc` and `.mrcs`, which many users prefer to open with dedicated tools such as `3dmod`.

### 9.4 Metadata stored by CryoPal

The gallery stores user-curated metadata such as:

- rating
- tags
- linked files

This turns the gallery into more than a viewer. It becomes a lightweight QC and annotation layer for your project.

## 10. Processing: WARP

`Processing: WARP` is the main tab for Warp/WarpTools-based processing.

### 10.1 General idea

This tab lets you:

- choose a WARP job type
- inspect and edit parameters
- preview the final command
- run locally
- submit to Slurm
- schedule commands for later execution
- review job history

CryoPal uses a job catalog for WARP jobs so that job definitions are managed centrally and can be updated consistently.

### 10.2 Parameter behavior

Many parameters can be pre-filled from:

- dataset metadata
- known paths
- saved default parameters

This reduces repetitive manual entry and helps keep commands consistent across datasets.

### 10.3 Local execution vs Slurm

For jobs that can run locally or via Slurm:

- `Run locally` uses the selected local environment, if any
- `Submit to Slurm` uses a selected Slurm profile plus optional per-run overrides

### 10.4 Environments

When running locally, CryoPal can activate a named environment before executing the command. This is useful when WarpTools, M, or related software are installed in dedicated conda or virtual environments.

### 10.5 Scheduled jobs

You can schedule WARP jobs and run them later. Scheduled jobs are tracked in job history. CryoPal also supports queue-aware behavior so that scheduled sets can wait for currently running sets rather than colliding immediately.

### 10.6 Examples of use

Typical WARP workflows in CryoPal include:

- creating settings
- importing tilt-series information
- frame-series processing
- CTF estimation
- tilt-series reconstruction
- particle export
- moving data while keeping CryoPal path knowledge synchronized

## 11. Processing: M

`Processing: M` is used for MTools and MCore workflows around M populations.

### 11.1 Creating or importing M populations

CryoPal supports both:

- `Create new M population`
- `Import existing M population`

When importing an existing `.population` file, CryoPal parses internal information such as:

- the population file path
- the population name
- species entries
- source entries

This is important because some M commands require:

- a population file
- a selected species
- a selected source

### 11.2 Population-aware defaults

CryoPal can automatically use the selected M population for commands that require a population argument. Likewise, species and source selections are exposed as dropdowns where appropriate.

### 11.3 Job execution model

As in other processing tabs, you can:

- preview commands
- schedule them
- run them locally
- submit them to Slurm
- inspect history entries later

### 11.4 When to use this tab

Use `Processing: M` when your workflow has moved beyond basic Warp reconstruction and you are working with M-based population/species/source structures.

## 12. Processing: TS jobs

`Processing: TS jobs` is the place for tilt-series-level downstream jobs.

### 12.1 TS processing list

This tab works around a `TS processing list`. You can populate it by:

- selecting a dataset and TS directly
- adding the currently selected tomogram from the gallery

This is useful when you want to run the same downstream job on a defined subset of tilt series.

### 12.2 Job examples

Depending on your CryoPal setup, this tab can host jobs such as:

- PyTom template matching
- extraction-related downstream steps
- slabify
- CryoLithe-related processing
- MemBrain-style membrane segmentation workflows

### 12.3 Path reuse

One of the strengths of this tab is that it reuses information already known to CryoPal:

- dataset membership
- TS names
- file-registry matches
- tomogram paths
- template- or mask-related roles

This means less manual path entry and fewer mismatches.

### 12.4 History and scheduling

TS jobs maintain their own history. Scheduled jobs can be run locally or submitted to Slurm, and you can inspect details of previous runs by double-clicking history entries.

## 13. Processing: Particle jobs

The `Processing: Particle jobs` tab is focused on STAR-file and particle-analysis workflows.

### 13.1 Available job types

CryoPal currently supports several particle-oriented operations, including:

- `Export particles`
- `Distance clean`
- `Intersect .star-files`
- `Plot particle abundance`
- `Plot classification convergence`
- `Merge/Split .star-files`

### 13.2 Export particles

`Export particles` uses dataset-aware path resolution to construct WarpTools export commands. In practice, this means CryoPal can resolve key dataset information internally, including the relevant settings file, instead of forcing you to rebuild the command from scratch each time.

### 13.3 Distance clean and STAR intersections

These jobs are useful when you want to:

- remove close duplicates
- compare particle sets
- identify shared or unique particles

### 13.4 Plot particle abundance

This plotting job helps you compare particle numbers or densities across conditions and datasets. It is intended as an analysis aid within CryoPal, not just a command launcher.

CryoPal can optionally save particle plots into project history so that they can be reopened later from job details.

### 13.5 Plot classification convergence

This job analyzes iteration series such as `run_it???_data.star` files and creates plots for:

- class occupancy per iteration
- convergence, based on class changes between iterations

This is useful for monitoring classification stability over time.

### 13.6 Merge/Split STAR files

This job can either:

- merge multiple `.star` files into one
- split one `.star` file into TS-specific outputs

These operations are deterministic and file-based. They do not depend on CryoPal’s central TS catalog for the actual split/merge logic.

### 13.7 Busy dialogs for longer calculations

For computationally heavier particle-analysis tasks, CryoPal shows a progress dialog so users know that the application is still working. This helps avoid confusion when reading large STAR files or generating plots.

## 14. Processing: Custom jobs

The `Processing: Custom jobs` tab lets advanced users define their own reusable job types.

### 14.1 Why custom jobs are useful

Not every cryo-ET workflow fits neatly into Warp, M, or built-in TS/particle operations. Custom jobs give you a way to integrate:

- lab-specific scripts
- wrappers around third-party tools
- internal pipelines
- small helper commands

### 14.2 Building a custom job

For each custom job, you can define:

- job name
- default local environment
- description
- base command template
- custom parameter rows

Parameters can be defined with input types such as:

- text
- path
- file
- boolean
- TS-selection roles from the file registry
- file-pattern driven selections

### 14.3 Reusing file registry roles

One especially powerful feature is the reuse of file-registry roles in custom-job inputs. For example, if you define a role called `Segmentation` with TS matching enabled, that role becomes available as a custom-job input type.

This lets you build custom tools that automatically consume the correct per-TS file.

## 15. Shortcuts

The `Shortcuts` tab acts as a lightweight dashboard for launching small scripted routines.

### 15.1 What shortcuts are for

Shortcuts are not primarily for tracked processing jobs. They are for convenience actions such as:

- opening a software GUI
- activating an environment and starting a tool
- jumping into a known working directory and launching something there

### 15.2 Shortcut tiles

Each shortcut appears as a tile. You can:

- create a new shortcut
- assign it a title
- define a multi-line script
- choose a tile color

Double-clicking the tile launches the shortcut in its own log window.

### 15.3 Managing shortcuts

Under `Settings > Manage shortcuts`, you can:

- add
- edit
- remove
- clone
- import
- export

shortcuts as reusable project assets.

## 16. Local environments

CryoPal supports named local environments for jobs that run locally.

### 16.1 Why this exists

In real cryo-ET workflows, different software stacks are often installed in different environments. Examples include:

- WarpTools environment
- MTools environment
- MemBrain environment
- PyTom environment

### 16.2 Managing environments

Use `Settings > Manage environments` to define entries consisting of:

- a title
- an activation command

Examples:

- `conda activate warp3`
- `source /path/to/venv/bin/activate`
- `uv run --project /path/to/project`

### 16.3 Defaults

Environment selection can be saved as a default per job type so that you do not need to choose it again for every run.

## 17. Slurm submission

CryoPal supports Slurm-based execution for many jobs.

### 17.1 Slurm profiles

Use `Settings > Slurm submission` to define profiles for your cluster.

Profiles can include:

- modular `sbatch` header flags
- descriptions
- values
- environment/module setup commands
- conda activation lines if needed

This structure is flexible enough to support cluster-specific flags and custom headers.

### 17.2 Job-specific overrides

When submitting a job to Slurm, CryoPal can show the selected profile’s core parameters so that you can override them for that specific run.

### 17.3 Scheduled submissions

For scheduled jobs, CryoPal supports different submission strategies. Depending on the context, you may be able to:

- submit each job separately in sequence
- submit a joined batch collectively in one Slurm job

This is useful when you want either strict per-job control or a single grouped submission script.

## 18. Job history

Job history is an important part of CryoPal’s usability.

### 18.1 What is stored

History entries can include:

- job name
- dataset or population context
- timestamp
- action status
- command text
- parameters
- execution mode
- Slurm metadata
- derived artifacts
- optional plot data for particle jobs

### 18.2 Statuses and queue states

Depending on what has happened, entries may appear as:

- scheduled
- waiting
- running
- completed states such as ran or submitted

CryoPal also uses color coding in job-history views to make these states easier to understand.

### 18.3 Detail windows

Double-clicking a history entry opens a detail window. This is useful for:

- reviewing exactly what was run
- copying parameters into the current form
- checking output-related metadata
- revisiting saved particle plots

## 19. Check paths

`Settings > Check paths` is one of the most useful maintenance tools in CryoPal.

### 19.1 What it does

CryoPal checks the existence of all known files and paths it can resolve for the current project, such as:

- raw-data locations
- MDOCs
- settings files
- tomograms
- thumbnails
- aligned stacks
- custom file-registry roles

### 19.2 Summary vs detail mode

If everything is found, CryoPal reports success and offers a detail view.

If things are missing, CryoPal reports the missing items and lets you inspect a detailed categorized listing. The report distinguishes between:

- items missing completely for a dataset
- items missing only for specific tilt series

This makes `Check paths` very useful after moving data, changing storage mounts, or importing legacy datasets.

## 20. Exporting information

CryoPal supports multiple export routes for documentation and downstream bookkeeping.

### 20.1 Export job history

Use `File > Export job history...` to write job-history information to CSV. This export includes jobs across CryoPal processing areas and can include a processing-tab column to preserve context.

### 20.2 Export file paths

Use `File > Export file paths...` to export known, existing file paths to CSV. This export is based on the same central path knowledge used by `Check paths`.

This is useful when you want:

- a machine-readable inventory of project files
- an input table for downstream software
- a quick audit of what CryoPal currently resolves

### 20.3 Export and import settings bundles

Use:

- `Settings > Export .cryopal.settings-file`
- `Settings > Import .cryopal.settings-file`

These settings bundles can contain:

- preferences
- viewer defaults
- default parameters
- file-registry patterns
- Slurm profiles
- environments
- custom job types
- shortcuts
- appearance

The import/export system is granular. You can choose complete categories or specific entries within them.

## 21. Viewer defaults

Use `Settings > Configure viewer defaults` to control how CryoPal opens files externally.

### 21.1 Default behavior

By default, files can be opened with the operating system’s default associated application.

### 21.2 Exceptions

You can define exceptions mapping file extensions to custom commands. A common example is opening:

- `.mrc`
- `.mrcs`

with `3dmod` rather than the system default.

### 21.3 Project-specific plus global behavior

Viewer-default changes are stored:

- in the current project
- and as the new global CryoPal default

If a project has no custom viewer configuration, the global default is used.

## 22. Set default parameters

`Settings > Set default parameters` lets you define reusable defaults for built-in job definitions.

### 22.1 Why this matters

If you always use the same:

- environment
- Slurm settings
- numerical defaults
- path patterns

you do not want to re-enter them for each job.

### 22.2 Scope

Defaults can cover built-in commands across multiple areas, including:

- WARP processing
- M processing
- TS jobs
- particle jobs
- file-registry-related defaults

## 23. Appearance and preferences

### 23.1 Appearance

Use `Settings > Appearance` to customize the look of CryoPal for the current project.

### 23.2 Preferences

Use `Settings > Set preferences` for project-level behavioral options. One example is whether particle plots should be stored with history entries for later reopening.

## 24. Debug mode

`Settings > Debug mode` is for safe command inspection and workflow debugging.

### 24.1 What Debug mode does

In Debug mode:

- jobs are simulated instead of really executed
- commands are shown as if they were run
- errors related to real execution are suppressed where appropriate
- project changes from the debug session are temporary
- project saving is disabled
- a verbose debug-output window is shown

### 24.2 Why this is useful

Use Debug mode when you want to:

- inspect command construction
- test scheduling logic
- validate Slurm scripts
- debug parameter propagation
- teach a workflow without touching real data

### 24.3 Important limitation

Changes made in Debug mode are not meant to become permanent project state. When you exit Debug mode, the temporary debug progress is discarded.

## 25. Recommended workflow patterns

### 25.1 For new datasets

Recommended sequence:

1. Add the dataset in `Project Overview`.
2. Confirm dataset metadata and paths.
3. Check the `File registry`.
4. Run WARP setup and reconstruction jobs.
5. Verify results with `Check paths`.
6. Inspect reconstructed tomograms in `Tomogram Gallery`.
7. Proceed to TS, M, or particle workflows.

### 25.2 For already processed datasets

Recommended sequence:

1. Import the dataset.
2. Run `Check paths`.
3. Verify thumbnails, tomograms, and settings associations.
4. Use the gallery and downstream tabs without re-creating upstream metadata.

### 25.3 For cluster-based processing

Recommended sequence:

1. Define one or more Slurm profiles.
2. Define local environments for any local preprocessing or quick tests.
3. Save sensible default parameters.
4. Schedule jobs first if you want to build a queue deliberately.
5. Use history and exports to document what was submitted.

## 26. Troubleshooting tips

### 26.1 A file is not found

Check:

- dataset paths in `Project Overview`
- file-role rules in `File registry`
- `Check paths`
- whether TS matching is enabled when it should be
- whether a manual override is needed

### 26.2 A command preview looks wrong

Check:

- the selected dataset or population
- saved default parameters
- selected local environment
- selected Slurm profile
- whether you are in Debug mode

### 26.3 A tomogram does not appear in the gallery

Check:

- whether the `.mrc` exists
- whether the thumbnail exists or must be imported
- whether file-registry rules point to the expected location
- whether the dataset was imported with the expected processing paths

### 26.4 A job cannot find the correct software

Check:

- `Settings > Manage environments`
- whether the correct environment is selected for local execution
- whether the Slurm profile contains the necessary module or activation commands

### 26.5 Paths changed after moving data

Use:

- `Check paths`
- `File registry`
- dataset import or update workflows

CryoPal is designed to help recover from moved or reorganized data, but it still depends on correct path knowledge.

## 27. Best practices

- Use descriptive dataset names early.
- Keep pixel size, exposure, and dimensions accurate.
- Treat the file registry as a shared infrastructure layer, not as an afterthought.
- Use gallery ratings and tags to document QC decisions.
- Save default parameters for recurring jobs.
- Define environments and Slurm profiles before large runs.
- Export job history and file paths for traceability.
- Use Debug mode before major workflow changes or when onboarding new users.

## 28. Summary

CryoPal is most powerful when used as a central coordination layer for cryo-ET data processing rather than as a simple command launcher. If your datasets, file roles, environments, and defaults are configured well, the rest of the workflow becomes much smoother:

- commands are easier to construct
- fewer paths must be entered manually
- processing history becomes easier to trace
- QC and particle-analysis outputs stay connected to the project
- cluster and local execution can coexist in a controlled way

For most users, the key to getting value from CryoPal is to spend a little time setting up the project structure correctly at the beginning. That investment usually pays off quickly once multiple datasets, many tilt series, and repeated processing steps are involved.
