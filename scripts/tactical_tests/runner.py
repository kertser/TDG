"""
Tactical Test Runner — main entry point.

Usage:
    python -m scripts.tactical_tests.runner [options]

    --keep-data          Don't clean up DB data after tests
    --scenario N         Run only scenario N, 0=all
    --category CAT       Run only a category: engine, llm, tactical, historical, statistical
    --skip-llm           Skip LLM scenarios (no API key needed)
    --runs N             Override statistical run count (default: scenario-defined)
    --verbose / -v       Verbose logging
    --report PATH        Output report path

Categories:
    engine      S01-S12  Deterministic engine mechanics
    llm         S13-S18  LLM order-parsing pipeline
    tactical    S19-S24  Complex tactical situations
    historical  S25-S29  Historical battle recreations
    statistical S30-S32  Probabilistic outcome testing
"""
from __future__ import annotations

import os
# Must be set BEFORE any backend imports to prevent echo=True SQL logging
os.environ["DEBUG"] = "false"

import asyncio
import argparse
import logging
import sys
import time
from pathlib import Path

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


def _check_openai_key() -> bool:
    """Check if OpenAI API key is available."""
    from backend.config import settings
    return bool(settings.OPENAI_API_KEY)


async def _run_statistical(scenario, evaluator, keep_data: bool, runs: int, logger) -> "StatisticalResult":
    """Run a scenario multiple times and aggregate results."""
    from scripts.tactical_tests.executor import ScenarioExecutor
    from scripts.tactical_tests.collector import ScenarioResult, StatisticalResult

    stat = StatisticalResult(
        scenario_name=scenario.name,
        scenario_description=scenario.description,
    )

    for run_idx in range(runs):
        logger.info("    Run %d/%d...", run_idx + 1, runs)
        executor = ScenarioExecutor(scenario, keep_data=keep_data)
        try:
            result = await executor.run()
            result.run_index = run_idx
        except Exception as e:
            logger.warning("    Run %d failed: %s", run_idx + 1, e)
            result = ScenarioResult(
                scenario_name=scenario.name,
                scenario_description=scenario.description,
                ticks_run=0,
                errors=[str(e)],
                run_index=run_idx,
            )

        # Evaluate per-run assertions
        assertions = scenario.build_assertions()
        result.assertions = evaluator.evaluate(result, assertions)
        result.passed = all(a.passed for a in result.assertions) and not result.errors
        stat.runs.append(result)
        stat.total_duration += result.duration_seconds

    # Evaluate statistical assertions
    stat_assertions = scenario.build_statistical_assertions()
    stat.assertions = evaluator.evaluate_statistical(stat, stat_assertions)
    stat.passed = all(a.passed for a in stat.assertions)

    return stat


async def main():
    parser = argparse.ArgumentParser(description="KShU Tactical Engine Test Runner")
    parser.add_argument("--keep-data", action="store_true",
                        help="Don't clean up DB data after tests")
    parser.add_argument("--scenario", type=int, default=0,
                        help="Run only scenario N (by list index), 0=all")
    parser.add_argument("--category", type=str, default=None,
                        choices=["engine", "llm", "tactical", "historical", "statistical"],
                        help="Run only a specific category")
    parser.add_argument("--skip-llm", action="store_true",
                        help="Skip LLM scenarios (no API key needed)")
    parser.add_argument("--runs", type=int, default=0,
                        help="Override statistical run count")
    parser.add_argument("--verbose", "-v", action="store_true",
                        help="Verbose logging")
    parser.add_argument("--report", type=str, default=None,
                        help="Output report path (default: scripts/tactical_tests/report.html)")
    args = parser.parse_args()

    # Configure logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # Suppress noisy loggers
    for name in ["sqlalchemy", "sqlalchemy.engine", "sqlalchemy.engine.Engine",
                  "sqlalchemy.pool", "sqlalchemy.orm", "asyncpg", "aiosqlite"]:
        logging.getLogger(name).setLevel(logging.ERROR)
        logging.getLogger(name).propagate = False
    os.environ["DEBUG"] = "false"
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)

    logger = logging.getLogger("tactical_tests")

    # Import after path setup
    from backend.database import engine, Base
    from sqlalchemy import text

    # Ensure DB tables exist
    logger.info("Ensuring database tables exist...")
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        await conn.run_sync(Base.metadata.create_all)

    # ── Import ALL scenario classes ──
    # Engine scenarios (S01-S12)
    from scripts.tactical_tests.scenarios.s01_infantry_assault import InfantryAssault
    from scripts.tactical_tests.scenarios.s02_armored_breakthrough import ArmoredBreakthrough
    from scripts.tactical_tests.scenarios.s03_hasty_defense import HastyDefense
    from scripts.tactical_tests.scenarios.s04_recon_concealment import ReconConcealment
    from scripts.tactical_tests.scenarios.s05_artillery_coordination import ArtilleryCoordination
    from scripts.tactical_tests.scenarios.s06_meeting_engagement import MeetingEngagement
    from scripts.tactical_tests.scenarios.s07_withdrawal import WithdrawalUnderPressure
    from scripts.tactical_tests.scenarios.s08_night_ops import NightOperations
    from scripts.tactical_tests.scenarios.s09_resupply import ResupplyUnderFire
    from scripts.tactical_tests.scenarios.s10_weather_storm import WeatherStormAssault
    from scripts.tactical_tests.scenarios.s11_morale_cascade import MoraleCascade
    from scripts.tactical_tests.scenarios.s12_combined_arms import CombinedArmsFullSpectrum
    # LLM scenarios (S13-S18)
    from scripts.tactical_tests.scenarios.s13_llm_clear_orders_en import LLMClearOrdersEN
    from scripts.tactical_tests.scenarios.s14_llm_clear_orders_ru import LLMClearOrdersRU
    from scripts.tactical_tests.scenarios.s15_llm_complex_orders import LLMComplexOrders
    from scripts.tactical_tests.scenarios.s16_llm_nonsense import LLMNonsenseOrders
    from scripts.tactical_tests.scenarios.s17_llm_status_acks import LLMStatusAndAcks
    from scripts.tactical_tests.scenarios.s18_llm_natural_language import LLMNaturalLanguage
    # Tactical scenarios (S19-S24)
    from scripts.tactical_tests.scenarios.s19_fire_and_maneuver import FireAndManeuver
    from scripts.tactical_tests.scenarios.s20_river_crossing import RiverCrossing
    from scripts.tactical_tests.scenarios.s21_minefield_breach import MinefieldBreach
    from scripts.tactical_tests.scenarios.s22_urban_clearing import UrbanClearing
    from scripts.tactical_tests.scenarios.s23_smoke_advance import SmokeAndAdvance
    from scripts.tactical_tests.scenarios.s24_defense_in_depth import DefenseInDepth
    # Historical scenarios (S25-S29)
    from scripts.tactical_tests.scenarios.s25_kursk_armor import KurskArmorClash
    from scripts.tactical_tests.scenarios.s26_normandy_beach import NormandyBeachAssault
    from scripts.tactical_tests.scenarios.s27_golan_heights import GolanHeightsDefense
    from scripts.tactical_tests.scenarios.s28_73_easting import SeventyThreeEasting
    from scripts.tactical_tests.scenarios.s29_stalingrad_urban import StalingradUrban
    # Statistical scenarios (S30-S32)
    from scripts.tactical_tests.scenarios.s30_stat_open_detection import StatOpenDetection
    from scripts.tactical_tests.scenarios.s31_stat_forest_concealment import StatForestConcealment
    from scripts.tactical_tests.scenarios.s32_stat_combat_variance import StatCombatVariance

    ALL_SCENARIOS = [
        # Engine (S01-S12)
        InfantryAssault(),
        ArmoredBreakthrough(),
        HastyDefense(),
        ReconConcealment(),
        ArtilleryCoordination(),
        MeetingEngagement(),
        WithdrawalUnderPressure(),
        NightOperations(),
        ResupplyUnderFire(),
        WeatherStormAssault(),
        MoraleCascade(),
        CombinedArmsFullSpectrum(),
        # LLM (S13-S18)
        LLMClearOrdersEN(),
        LLMClearOrdersRU(),
        LLMComplexOrders(),
        LLMNonsenseOrders(),
        LLMStatusAndAcks(),
        LLMNaturalLanguage(),
        # Tactical (S19-S24)
        FireAndManeuver(),
        RiverCrossing(),
        MinefieldBreach(),
        UrbanClearing(),
        SmokeAndAdvance(),
        DefenseInDepth(),
        # Historical (S25-S29)
        KurskArmorClash(),
        NormandyBeachAssault(),
        GolanHeightsDefense(),
        SeventyThreeEasting(),
        StalingradUrban(),
        # Statistical (S30-S32)
        StatOpenDetection(),
        StatForestConcealment(),
        StatCombatVariance(),
    ]

    # ── Filter scenarios ──
    scenarios = ALL_SCENARIOS

    if args.scenario > 0:
        if args.scenario > len(ALL_SCENARIOS):
            logger.error("Scenario %d does not exist (max: %d)", args.scenario, len(ALL_SCENARIOS))
            sys.exit(1)
        scenarios = [ALL_SCENARIOS[args.scenario - 1]]
    elif args.category:
        scenarios = [s for s in ALL_SCENARIOS if s.category == args.category]
        if not scenarios:
            logger.error("No scenarios found for category '%s'", args.category)
            sys.exit(1)

    # Check LLM availability
    has_openai = _check_openai_key()
    if args.skip_llm:
        scenarios = [s for s in scenarios if s.category != "llm"]
        logger.info("⏭  Skipping pure LLM scenarios (--skip-llm)")
        logger.info("   Note: engine/tactical/historical scenarios still use LLM for order parsing")
    elif not has_openai:
        llm_count = sum(1 for s in scenarios if s.category == "llm")
        if llm_count > 0:
            logger.warning("⚠ No OPENAI_API_KEY found. Pure LLM scenarios will be skipped.")
            logger.warning("  Engine/tactical scenarios will use keyword fallback for order parsing.")
            scenarios = [s for s in scenarios if s.category != "llm"]

    from scripts.tactical_tests.executor import ScenarioExecutor
    from scripts.tactical_tests.evaluator import Evaluator
    from scripts.tactical_tests.report_gen import generate_report

    evaluator = Evaluator()
    results = []
    stat_results = []

    total_start = time.monotonic()

    # Count by category
    cats = {}
    for s in scenarios:
        cats[s.category] = cats.get(s.category, 0) + 1

    logger.info("=" * 70)
    logger.info("🎯 KShU TACTICAL ENGINE TEST FRAMEWORK")
    logger.info("=" * 70)
    logger.info("Running %d scenarios: %s\n",
                len(scenarios),
                ", ".join(f"{v} {k}" for k, v in sorted(cats.items())))

    for i, scenario in enumerate(scenarios):
        idx = i + 1
        cat_icon = {"engine": "⚙️", "llm": "🤖", "tactical": "⚔️",
                     "historical": "📜", "statistical": "📊"}.get(scenario.category, "?")

        logger.info("━" * 60)
        logger.info("%s [%d/%d] %s", cat_icon, idx, len(scenarios), scenario.name)
        logger.info("  %s", scenario.description[:80])
        logger.info("  Category: %s | Ticks: %d | Language: %s",
                     scenario.category, scenario.ticks, scenario.language)

        # ── Statistical scenarios ──
        if scenario.category == "statistical":
            runs = args.runs if args.runs > 0 else scenario.statistical_runs
            logger.info("  Statistical: %d runs", runs)
            logger.info("━" * 60)

            stat = await _run_statistical(scenario, evaluator, args.keep_data, runs, logger)
            stat_results.append(stat)

            status = "✅ PASS" if stat.passed else "❌ FAIL"
            logger.info("\n  %s — %d/%d stat assertions passed (%.1fs, %d runs)",
                         status, stat.assertions_passed, stat.assertions_total,
                         stat.total_duration, stat.num_runs)
            for a in stat.assertions:
                icon = "  ✅" if a.passed else "  ❌"
                logger.info("%s %s", icon, a.description)
                logger.info("     → %s", a.detail[:120])

            # Also add individual run results to flat list for report
            for r in stat.runs:
                results.append(r)
            logger.info("")
            continue

        logger.info("━" * 60)

        # ── Normal execution ──
        executor = ScenarioExecutor(scenario, keep_data=args.keep_data)
        try:
            result = await executor.run()
        except Exception as e:
            logger.error("Scenario execution failed: %s", e)
            import traceback
            traceback.print_exc()
            from scripts.tactical_tests.collector import ScenarioResult
            result = ScenarioResult(
                scenario_name=scenario.name,
                scenario_description=scenario.description,
                ticks_run=0,
                errors=[f"Fatal: {type(e).__name__}: {e}"],
                category=scenario.category,
            )

        # Evaluate assertions (engine + LLM for all scenarios)
        assertions = scenario.build_assertions()
        if scenario.category == "llm":
            assertions.extend(scenario.build_llm_assertions())
        # Auto-add LLM bulk assertions for ALL scenarios with LLM pipeline orders
        if scenario.use_llm_pipeline or any(
            o.get("use_llm_pipeline") for o in scenario.build_orders({})
            if isinstance(o, dict)
        ):
            # Validate all orders were parsed by LLM
            assertions.append({
                "type": "llm_all_orders_parsed",
                "params": {},
                "description": "All orders successfully parsed by LLM pipeline",
            })
        assertion_results = evaluator.evaluate(result, assertions)
        result.assertions = assertion_results
        result.passed = all(a.passed for a in assertion_results) and not result.errors

        # Log results
        status = "✅ PASS" if result.passed else "❌ FAIL"
        logger.info("\n  %s — %d/%d assertions passed (%.1fs)",
                     status, result.assertions_passed, result.assertions_total,
                     result.duration_seconds)

        for a in assertion_results:
            icon = "  ✅" if a.passed else "  ❌"
            logger.info("%s %s", icon, a.description)
            if not a.passed:
                logger.info("     → %s", a.detail[:120])

        if result.errors:
            for err in result.errors:
                logger.error("  ⚠ %s", err[:120])

        # Log LLM pipeline details
        if result.order_snapshots:
            logger.info("  📋 LLM Pipeline Results:")
            for j, snap in enumerate(result.order_snapshots):
                status_icon = "✅" if not snap.error else "❌"
                logger.info("    %s Order %d: '%s'",
                             status_icon, j, snap.original_text[:50])
                if snap.error:
                    logger.info("       Error: %s", snap.error[:80])
                else:
                    logger.info("       Class=%s Type=%s Lang=%s Conf=%.2f Tier=%s",
                                 snap.classification, snap.order_type,
                                 snap.language, snap.confidence,
                                 snap.model_tier)

        results.append(result)
        logger.info("")

    # ── Generate report ──
    total_time = time.monotonic() - total_start
    report_path = args.report or str(
        Path(__file__).resolve().parent / "report.html"
    )
    generate_report(results, report_path, stat_results=stat_results)

    # ── Summary ──
    # Count by category
    cat_stats = {}
    for r in results:
        cat = r.category
        if cat not in cat_stats:
            cat_stats[cat] = {"passed": 0, "failed": 0}
        if r.passed:
            cat_stats[cat]["passed"] += 1
        else:
            cat_stats[cat]["failed"] += 1

    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed
    total_assertions = sum(r.assertions_total for r in results)
    passed_assertions = sum(r.assertions_passed for r in results)

    logger.info("=" * 70)
    logger.info("📊 FINAL SUMMARY")
    logger.info("=" * 70)
    logger.info("  Scenarios: %d passed, %d failed, %d total", passed, failed, len(results))
    for cat, stats in sorted(cat_stats.items()):
        icon = {"engine": "⚙️", "llm": "🤖", "tactical": "⚔️",
                "historical": "📜", "statistical": "📊"}.get(cat, "?")
        logger.info("    %s %s: %d passed, %d failed",
                     icon, cat, stats["passed"], stats["failed"])

    # Statistical summary
    if stat_results:
        logger.info("  Statistical runs:")
        for sr in stat_results:
            icon = "✅" if sr.passed else "❌"
            logger.info("    %s %s: %d/%d assertions, %d runs",
                         icon, sr.scenario_name,
                         sr.assertions_passed, sr.assertions_total, sr.num_runs)

    logger.info("  Assertions: %d/%d passed", passed_assertions, total_assertions)
    logger.info("  Total time: %.1fs", total_time)
    logger.info("  Report: %s", report_path)
    logger.info("=" * 70)

    if failed > 0:
        logger.info("\n⚠ FAILED SCENARIOS:")
        for r in results:
            if not r.passed:
                logger.info("  ❌ %s", r.scenario_name)
                for a in r.assertions:
                    if not a.passed:
                        logger.info("     • %s: %s", a.description, a.detail[:80])

    # Return exit code
    stat_ok = all(sr.passed for sr in stat_results)
    return 0 if (failed == 0 and stat_ok) else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)





