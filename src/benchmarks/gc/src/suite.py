# Licensed to the .NET Foundation under one or more agreements.
# The .NET Foundation licenses this file to you under the MIT license.
# See the LICENSE file in the project root for more information.

from dataclasses import dataclass
from pathlib import Path
from typing import cast, Dict, Mapping, Optional, Sequence, Type

from .analysis.diffable import get_diffables
from .analysis.process_trace import ProcessedTraces
from .analysis.report import (
    print_diff_score_summary,
    get_run_metrics_for_diff,
    show_diff_from_diffables,
)
from .analysis.types import RunMetrics, SampleKind, SAMPLE_KIND_DOC

from .commonlib.bench_file import (
    AllocType,
    BenchFile,
    BenchOptions,
    Benchmark,
    CoreclrSpecifier,
    parse_bench_file,
    GCPerfSimArgs,
    MAX_ITERATIONS_FOR_ANALYZE_DOC,
    MemoryLoadOptions,
    TestConfigContainer,
    Config,
    Vary,
)
from .commonlib.get_built import get_corerun_path_from_core_root, get_latest_testbin_path
from .commonlib.collection_util import add, combine_mappings, is_empty
from .commonlib.command import Command, CommandKind, CommandsMapping, run_command_worker
from .commonlib.document import handle_doc, OutputOptions
from .commonlib.frozen_dict import FrozenDict
from .commonlib.host_info import read_this_machines_host_info
from .commonlib.option import option_or
from .commonlib.parse_and_serialize import load_yaml, write_yaml_file
from .commonlib.score_spec import ScoreElement, ScoreSpec
from .commonlib.type_utils import argument, with_slots
from .commonlib.util import ensure_empty_dir, RunErrorMap

from .exec.run_tests import run_test, RunArgs


SuiteCommand = str


@with_slots
@dataclass(frozen=True)
class SuiteFile:
    bench_files: Sequence[Path]
    command_groups: Mapping[str, Sequence[SuiteCommand]]


@with_slots
@dataclass(frozen=True)
class SuiteCreateArgs:
    path: Path = argument(name_optional=True, doc="Path to directory to write the suite to")
    coreclrs: Sequence[Path] = argument(
        doc="""
    One of:
    * A path to a '.yaml' file whose content is suitable to be the 'coreclrs' section of a benchfile.
    * A list of core_root directories.
    """
    )
    proc_count: Optional[int] = argument(
        default=None, doc="This is used for both thread and heap count."
    )
    overwrite: bool = argument(
        default=False,
        doc="If true, the suite directory will be deleted before creating a new suite.",
    )


def suite_create(args: SuiteCreateArgs) -> None:
    coreclrs: Mapping[str, CoreclrSpecifier] = _parse_coreclrs(args.coreclrs)
    options = BenchOptions(default_iteration_count=2, default_max_seconds=300)
    proc_count = option_or(args.proc_count, _get_default_proc_count())
    gcperfsim_path = get_latest_testbin_path("GCPerfSim")

    tests: Mapping[str, BenchFile] = {
        "normal_workstation": _create_scenario_normal_workstation(
            coreclrs, options, gcperfsim_path
        ),
        "normal_server": _create_scenario_normal_server(
            coreclrs, options, proc_count, gcperfsim_path
        ),
        "high_memory": _create_scenario_high_memory_load(
            coreclrs, options, proc_count, gcperfsim_path
        ),
        # TODO: use a low proc_count here?
        "low_memory_container": _create_scenario_low_memory_container(
            coreclrs, options, proc_count, gcperfsim_path
        ),
    }

    if args.path.exists():
        assert args.overwrite
    ensure_empty_dir(args.path)

    for test_name, bench_file in tests.items():
        write_yaml_file(args.path / f"{test_name}.yaml", bench_file, overwrite=True)

    command_groups: Mapping[str, Sequence[SuiteCommand]] = {
        # "diff": [
        #    f"diff $suite/{test_name}.yaml --vary coreclr --txt $suite/{test_name}.diff.txt"
        #    for test_name in tests.keys()
        # ]
    }
    suite_file = SuiteFile(
        bench_files=[Path(f"{test_name}.yaml") for test_name in tests],
        command_groups=command_groups,
    )
    write_yaml_file(args.path / "suite.yaml", suite_file, overwrite=True)


def _get_default_proc_count() -> int:
    n_processors = read_this_machines_host_info().n_logical_processors
    if n_processors <= 2:
        return n_processors
    elif n_processors <= 4:
        return n_processors - 1
    else:
        return n_processors - 2


SUITE_PATH_DOC = "Path to a 'suite.yaml' generated by `suite-create`."


@with_slots
@dataclass(frozen=True)
class SuiteRunArgs:
    suite_path: Path = argument(name_optional=True, doc=SUITE_PATH_DOC)
    overwrite: bool = argument(
        default=False,
        doc="""
    This is like the '--overwrite' argument to the normal 'run' command.
    """,
    )
    skip_where_exists: bool = argument(
        default=False,
        doc="This is like the '--skip-where-exists' argument to the normal 'run' command.",
    )


SuiteErrorMap = Dict[Path, RunErrorMap]


def suite_run(args: SuiteRunArgs) -> None:
    suite = load_yaml(SuiteFile, args.suite_path)
    suite_errors: SuiteErrorMap = {}

    for file in suite.bench_files:
        bench_file_path = args.suite_path.parent / file
        testrun_errors: RunErrorMap = {}

        print(f"\n=== {bench_file_path} ===\n")
        run_test(
            RunArgs(
                bench_file_path=bench_file_path,
                overwrite=args.overwrite,
                skip_where_exists=args.skip_where_exists,
            ),
            testrun_errors,
            True,
        )

        if not is_empty(testrun_errors):
            add(suite_errors, bench_file_path, testrun_errors)

    if is_empty(suite_errors):
        print("\n*** Suite run finished successfully! ***\n")
    else:
        print(
            f"\n*WARNING*: One or more tests in the suite encountered errors.\n"
            "\n*** Here is a summary of the problems found: ***"
        )
        for file, test_errors in suite_errors.items():
            print(f"\n========= {file} =========\n")
            for err in test_errors.values():
                err.print()


@with_slots
@dataclass(frozen=True)
class SuiteRunCommandArgs:
    suite_path: Path = argument(name_optional=True, doc=SUITE_PATH_DOC)
    command_name: str = argument(doc="Name of a command specified in 'suite.yaml'.")


def suite_run_command(args: SuiteRunCommandArgs) -> None:
    from .all_commands import ALL_COMMANDS  # pylint:disable=import-outside-toplevel

    suite = load_yaml(SuiteFile, args.suite_path)
    commands = suite.command_groups[args.command_name]
    for command in commands:
        # TODO: this will fail if there are spaces in path
        assert " " not in str(
            args.suite_path.parent
        ), "TODO: Substitution will fail if there are spaces in path"
        full_command = command.replace("$suite", str(args.suite_path.parent))
        print(f"=== {full_command} ===")
        run_command_worker(ALL_COMMANDS, full_command.split())


def _parse_coreclrs(coreclrs: Sequence[Path]) -> Mapping[str, CoreclrSpecifier]:
    if len(coreclrs) == 1 and coreclrs[0].name.endswith(".yaml"):
        return load_yaml(
            cast(Type[Mapping[str, CoreclrSpecifier]], Mapping[str, CoreclrSpecifier]), coreclrs[0]
        )
    else:
        return {chr(ord("a") + i): _parse_coreclr(coreclr) for i, coreclr in enumerate(coreclrs)}


def _parse_coreclr(coreclr: Path) -> CoreclrSpecifier:
    if get_corerun_path_from_core_root(coreclr).exists():
        return CoreclrSpecifier(core_root=coreclr)
    else:
        return CoreclrSpecifier(repo_path=coreclr)


def _normal_benchmarks(proc_count: int) -> Mapping[str, Benchmark]:
    tagb_factor = 0.5 if proc_count == 1 else 1
    return {
        "0gb": Benchmark(
            arguments=GCPerfSimArgs(tc=proc_count, tagb=300 * tagb_factor, tlgb=0), min_seconds=10
        ),
        "2gb": Benchmark(
            arguments=GCPerfSimArgs(tc=proc_count, tagb=300 * tagb_factor, tlgb=2, sohsi=50)
        ),
        # The pinning makes this test a lot slower, so allocate many fewer BG
        "2gb_pinning": Benchmark(
            arguments=GCPerfSimArgs(
                tc=proc_count, tagb=100 * tagb_factor, tlgb=2, sohsi=50, sohpi=50
            )
        ),
        # This must allocate 600GB to ensure the test isn't dominated by
        # the startup time of allocating the initial 20GB
        "20gb": Benchmark(
            arguments=GCPerfSimArgs(
                tc=proc_count, tagb=600 * tagb_factor, tlgb=20, sohsi=50, allocType=AllocType.simple
            )
        ),
    }


TYPICAL_SCORES: Mapping[str, ScoreSpec] = {
    "speed": FrozenDict(
        {
            "FirstToLastGCSeconds": ScoreElement(weight=1),
            "PauseDurationMSec_95P": ScoreElement(weight=1),
        }
    )
}

LOW_MEMORY_SCORES: Mapping[str, ScoreSpec] = combine_mappings(
    TYPICAL_SCORES,
    {
        "space": FrozenDict(
            {
                # Better to have a bigger size before, means we are using the space better
                "Gen2ObjSpaceBeforeMB_Sum_MeanWhereIsBlockingGen2": ScoreElement(weight=-1),
                # We want a lower size after (to have collected more)
                "Gen2ObjSizeAfterMB_Sum_MeanWhereIsBlockingGen2": ScoreElement(weight=1),
            }
        )
    },
)


def _create_scenario_normal_workstation(
    coreclrs: Mapping[str, CoreclrSpecifier], options: BenchOptions, gcperfsim: Path
) -> BenchFile:
    common_config = Config(complus_gcserver=False, complus_gcconcurrent=False)
    return BenchFile(
        vary=Vary.coreclr,
        test_executables={"defgcperfsim": gcperfsim},
        coreclrs=coreclrs,
        options=options,
        common_config=common_config,
        benchmarks=_normal_benchmarks(proc_count=1),
        scores=TYPICAL_SCORES,
    )


def _create_scenario_normal_server(
    coreclrs: Mapping[str, CoreclrSpecifier],
    options: BenchOptions,
    proc_count: int,
    gcperfsim: Path,
) -> BenchFile:
    common_config = Config(
        complus_gcserver=True, complus_gcconcurrent=False, complus_gcheapcount=proc_count
    )
    return BenchFile(
        vary=Vary.coreclr,
        test_executables={"defgcperfsim": gcperfsim},
        coreclrs=coreclrs,
        options=options,
        common_config=common_config,
        benchmarks=_normal_benchmarks(proc_count),
        scores=TYPICAL_SCORES,
    )


def _create_scenario_high_memory_load(
    coreclrs: Mapping[str, CoreclrSpecifier],
    options: BenchOptions,
    proc_count: int,
    gcperfsim: Path,
) -> BenchFile:
    common_config = Config(
        complus_gcserver=True, complus_gcconcurrent=False, complus_gcheapcount=proc_count
    )
    # TODO: Don't specify a percent, specify an amount remaining in GB
    configs: Mapping[str, Config] = {
        "80pct": Config(memory_load=MemoryLoadOptions(percent=80)),
        "90pct": Config(memory_load=MemoryLoadOptions(percent=90)),
    }
    benchmarks: Mapping[str, Benchmark] = {
        "a": Benchmark(arguments=GCPerfSimArgs(tc=proc_count, tagb=40, tlgb=5, sohsi=30, sohpi=50))
    }
    return BenchFile(
        vary=Vary.coreclr,
        test_executables={"defgcperfsim": gcperfsim},
        coreclrs=coreclrs,
        options=options,
        common_config=common_config,
        configs=configs,
        benchmarks=benchmarks,
        scores=LOW_MEMORY_SCORES,
    )


def _create_scenario_low_memory_container(
    coreclrs: Mapping[str, CoreclrSpecifier],
    options: BenchOptions,
    proc_count: int,
    gcperfsim: Path,
) -> BenchFile:
    # In a small container, coreclr should choose a low heap count for itself
    common_config = Config(
        complus_gcserver=True,
        complus_gcconcurrent=True,
        complus_gcheapcount=proc_count,
        # Remember, coreclr multiplies container size by 0.75 to get hard limit.
        container=TestConfigContainer(memory_mb=600),
    )
    benchmarks: Mapping[str, Benchmark] = {
        "tlgb0.2": Benchmark(
            arguments=GCPerfSimArgs(tc=proc_count, tagb=80, tlgb=0.2, sohsi=30, sohpi=50)
        )
    }
    return BenchFile(
        vary=Vary.coreclr,
        test_executables={"defgcperfsim": gcperfsim},
        coreclrs=coreclrs,
        options=options,
        common_config=common_config,
        benchmarks=benchmarks,
        scores=LOW_MEMORY_SCORES,
    )


@with_slots
@dataclass(frozen=True)
class SuiteDiffArgs:
    path: Path = argument(name_optional=True, doc=SUITE_PATH_DOC)
    max_iterations: Optional[int] = argument(default=None, doc=MAX_ITERATIONS_FOR_ANALYZE_DOC)
    sample_kind: SampleKind = argument(default=0, doc=SAMPLE_KIND_DOC)


def suite_diff(args: SuiteDiffArgs) -> None:
    suite = load_yaml(SuiteFile, args.path)
    bench_files = [parse_bench_file(args.path.parent / bf) for bf in suite.bench_files]

    for bench_file in bench_files:
        print(f"\n=== {bench_file.path} ===\n")
        run_metrics: RunMetrics = get_run_metrics_for_diff(
            include_summary=True, sort_by_metric=None, run_metrics=()
        )
        diffables = get_diffables(
            traces=ProcessedTraces(),
            paths=(bench_file.path,),
            run_metrics=run_metrics,
            machines_arg=None,
            vary=None,
            test_where=None,
            sample_kind=args.sample_kind,
            max_iterations=args.max_iterations,
            process=None,
        )
        # Print a summary -- write detailed diff to a file
        print_diff_score_summary(diffables)
        txt = Path(str(bench_file.path) + ".diff.txt")
        # Show summary too
        doc = show_diff_from_diffables(
            diffables,
            metrics_as_columns=False,
            sort_by_metric=None,
            min_difference_pct=0,
            sample_kind=0,
            include_summary=True,
        )
        handle_doc(doc, OutputOptions(txt=txt))


SUITE_COMMANDS: CommandsMapping = {
    "suite-create": Command(
        kind=CommandKind.suite, fn=suite_create, doc="Generate the default test suite."
    ),
    "suite-diff": Command(
        kind=CommandKind.suite,
        fn=suite_diff,
        doc="""
    Runs 'diff' on all tests in a suite.
    Outputs detailed diffs to files, and a brief summary on stdout.
    """,
    ),
    "suite-run": Command(kind=CommandKind.suite, fn=suite_run, doc="Run all tests in a suite."),
    "suite-run-command": Command(
        hidden=True, kind=CommandKind.suite, fn=suite_run_command, doc="WIP"
    ),
}
