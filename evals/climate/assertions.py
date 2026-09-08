"""Climate Eval hard assertion 求值。"""

from __future__ import annotations

from typing import Any

from evals.climate.models import AssertionResult, HardAssertionSpec, TraceRecord

# synthetic 仅验证 wiring；执行类断言由 real_offline 求值
WIRING_ASSERTION_TYPES = frozenset({"tool_sequence"})


def evaluate_hard_assertions(
    trace: TraceRecord,
    assertions: list[HardAssertionSpec | dict[str, Any]],
) -> list[AssertionResult]:
    """对 Trace 执行硬断言；任一失败不抛异常，由调用方决定退出码。"""
    results: list[AssertionResult] = []
    for raw in assertions:
        spec = raw if isinstance(raw, HardAssertionSpec) else HardAssertionSpec.model_validate(raw)
        results.append(_evaluate_one(trace, spec))
    return results


def _evaluate_one(trace: TraceRecord, spec: HardAssertionSpec) -> AssertionResult:
    actual_names = [item.name for item in trace.tool_calls]
    if spec.type == "tool_sequence":
        expected = spec.expected
        if not isinstance(expected, list):
            return _fail(spec, "tool_sequence 的 expected 必须是列表")
        passed = actual_names == expected
        return _ok(
            spec,
            passed,
            "ok" if passed else f"工具序列不匹配: actual={actual_names} expected={expected}",
        )
    if spec.type == "final_run_status":
        passed = trace.final_run_status == spec.expected
        return _ok(
            spec,
            passed,
            "ok" if passed else f"最终状态不匹配: {trace.final_run_status!r} != {spec.expected!r}",
        )
    if spec.type == "error_code":
        codes = [item.error_code for item in trace.tool_calls if item.error_code]
        passed = spec.expected in codes
        return _ok(spec, passed, "ok" if passed else f"未找到错误码 {spec.expected!r}")
    if spec.type == "all_tool_results_ok":
        passed = bool(trace.tool_calls) and all(item.is_error is False for item in trace.tool_calls)
        return _ok(spec, passed, "ok" if passed else "存在失败的 ToolResult")
    if spec.type == "trace_mode":
        passed = trace.mode.value == spec.expected
        return _ok(
            spec,
            passed,
            "ok" if passed else f"mode 不匹配: {trace.mode.value!r} != {spec.expected!r}",
        )
    if spec.type == "context_version_monotonic":
        versions = list(trace.context_versions)
        if not versions:
            versions = [item.context_version for item in trace.tool_calls if item.context_version is not None]
        passed = _is_monotonic(versions, mutating_calls=trace.tool_calls)
        return _ok(spec, passed, "ok" if passed else f"Context version 非单调: {versions}")
    if spec.type == "artifacts_match_manifest":
        return _artifacts_match(trace, spec)
    if spec.type == "report_relative_links":
        return _report_relative_links(trace, spec)
    if spec.type == "inspect_profile":
        return _inspect_profile(trace, spec)
    if spec.type == "source_unmodified":
        recovery = trace.recovery or {}
        passed = recovery.get("source_unmodified") is True
        return _ok(spec, passed, "ok" if passed else "源文件被修改或未记录")
    if spec.type == "network_isolated":
        passed = trace.network_isolated is True
        return _ok(spec, passed, "ok" if passed else "未启用禁网或隔离失败")
    if spec.type == "recovery_from_disk":
        recovery = trace.recovery or {}
        passed = (
            recovery.get("session_boundary") is True
            and recovery.get("session1_destroyed") is True
            and recovery.get("read_context_before_continue") is True
            and recovery.get("recovery_source") == "disk_context"
        )
        return _ok(spec, passed, "ok" if passed else f"恢复证据不足: {recovery}")
    if spec.type == "no_inherited_metadata":
        recovery = trace.recovery or {}
        passed = recovery.get("inherited_tool_metadata") is False
        return _ok(spec, passed, "ok" if passed else "第二会话继承了旧 tool_metadata")
    if spec.type == "tools_executed_real":
        passed = (
            trace.synthetic is False
            and trace.tools_executed is True
            and trace.model_invoked is False
            and trace.counts_toward_real_pass_rate is True
        )
        return _ok(spec, passed, "ok" if passed else "未证明真实工具执行或误标 synthetic")
    if spec.type == "model_and_tools_real":
        passed = (
            trace.synthetic is False
            and trace.tools_executed is True
            and trace.model_invoked is True
            and trace.counts_toward_real_pass_rate is True
            and trace.mode.value == "real_agent"
        )
        return _ok(spec, passed, "ok" if passed else "未证明真实模型与工具执行")
    if spec.type == "climate_dag_order":
        return _climate_dag_order(trace, spec)
    if spec.type == "cds_mode_no_fallback":
        return _cds_mode_no_fallback(trace, spec)
    if spec.type == "duration_within_timeout":
        limit = spec.expected
        if not isinstance(limit, int):
            return _fail(spec, "duration_within_timeout 的 expected 必须是秒")
        passed = 0 <= trace.duration_ms <= limit * 1000
        return _ok(
            spec,
            passed,
            "ok" if passed else f"耗时超出 timeout: {trace.duration_ms}ms > {limit}s",
        )
    if spec.type == "unknown_tools_forbidden":
        allowed = spec.expected
        if not isinstance(allowed, list):
            return _fail(spec, "unknown_tools_forbidden 的 expected 必须是工具名列表")
        unknown = [item.name for item in trace.tool_calls if item.name not in allowed]
        passed = not unknown
        return _ok(spec, passed, "ok" if passed else f"未知工具: {unknown}")
    if spec.type == "tool_called":
        return _tool_called(trace, spec)
    if spec.type == "hook_event":
        return _hook_event(trace, spec)
    if spec.type == "hook_provenance":
        return _hook_provenance(trace, spec)
    if spec.type == "blocked_side_effect_free":
        return _blocked_side_effect_free(trace, spec)
    if spec.type == "report_quality_rules":
        return _report_quality_rules(trace, spec)
    if spec.type == "knowledge_hits_variable":
        return _knowledge_hits_variable(trace, spec)
    return _fail(spec, f"未知 hard assertion 类型: {spec.type}")


def _climate_dag_order(trace: TraceRecord, spec: HardAssertionSpec) -> AssertionResult:
    expected = spec.expected
    if not isinstance(expected, list) or not expected:
        return _fail(spec, "climate_dag_order 的 expected 必须是非空工具列表")
    names = [item.name for item in trace.tool_calls]
    last_index = -1
    missing: list[str] = []
    for name in expected:
        try:
            index = names.index(name)
        except ValueError:
            missing.append(name)
            continue
        if index < last_index:
            return _ok(spec, False, f"工具顺序违反依赖: {name} 出现过早")
        last_index = index
    if missing:
        return _ok(spec, False, f"缺少工具: {missing}")
    return _ok(spec, True, "ok")


def _cds_mode_no_fallback(trace: TraceRecord, spec: HardAssertionSpec) -> AssertionResult:
    acquire = [item for item in trace.tool_calls if item.name == "climate_acquire_data"]
    if not acquire:
        return _fail(spec, "未找到 climate_acquire_data")
    data = acquire[-1].output_redacted or {}
    requested = data.get("requested_mode")
    effective = data.get("effective_mode")
    passed = (
        acquire[-1].is_error is False
        and requested == "cds"
        and effective == "cds"
        and not data.get("fallback_reason")
    )
    return _ok(
        spec,
        passed,
        "ok" if passed else f"CDS 模式不成立: requested={requested} effective={effective}",
    )


def _hook_event(trace: TraceRecord, spec: HardAssertionSpec) -> AssertionResult:
    expected = spec.expected
    if not isinstance(expected, dict):
        return _fail(spec, "hook_event 的 expected 必须是映射")
    matched = False
    for item in trace.hook_events:
        if (
            item.event == expected.get("event")
            and item.tool_name == expected.get("tool_name")
            and item.blocked is expected.get("blocked")
        ):
            if expected.get("blocked") is True and not item.reason_code:
                continue
            matched = True
            break
    return _ok(spec, matched, "ok" if matched else f"未找到 Hook 事件: {expected}")


def _hook_provenance(trace: TraceRecord, spec: HardAssertionSpec) -> AssertionResult:
    write_calls = [item for item in trace.tool_calls if item.name == "climate_write_report"]
    if not write_calls:
        return _fail(spec, "未找到 climate_write_report")
    call = write_calls[-1]
    provenance = (call.output_redacted or {}).get("provenance")
    passed = (
        call.is_error is True
        and call.error_code == "CLIMATE_HOOK_BLOCKED"
        and call.error_code != "CLIMATE_INVALID_INPUT"
        and provenance == spec.expected
    )
    return _ok(
        spec,
        passed,
        "ok" if passed else f"阻断来源不是 Hook: code={call.error_code} provenance={provenance}",
    )


def _blocked_side_effect_free(trace: TraceRecord, spec: HardAssertionSpec) -> AssertionResult:
    recovery = trace.recovery or {}
    passed = (
        recovery.get("write_report_executed") is False
        and recovery.get("context_version_unchanged") is True
        and recovery.get("events_unchanged") is True
        and recovery.get("file_tree_unchanged") is True
        and recovery.get("hook_blocked_before_execute") is True
    )
    return _ok(spec, passed, "ok" if passed else f"blocked 场景仍有副作用: {recovery}")


def _report_quality_rules(trace: TraceRecord, spec: HardAssertionSpec) -> AssertionResult:
    """离线规则级报告质量；明确不是 Bench-85 / 论文 Report Score。"""
    expected = spec.expected
    if not isinstance(expected, dict):
        return _fail(spec, "report_quality_rules 的 expected 必须是映射")
    recovery = trace.recovery or {}
    text = str(recovery.get("report_text") or "")
    relative = recovery.get("report_has_relative_plot")
    absolute = recovery.get("report_has_absolute_workspace")
    for item in trace.tool_calls:
        if item.name == "climate_write_report":
            flags = item.output_redacted or {}
            if relative is None:
                relative = flags.get("has_relative_plot")
            if absolute is None:
                absolute = flags.get("has_absolute_workspace")
    score = recovery.get("report_rule_score")
    if not isinstance(score, int):
        from openharness.climate.validate import score_report_markdown

        score = score_report_markdown(text)
    min_chars = int(expected.get("min_chars") or 0)
    headings = expected.get("required_headings") or []
    missing = [item for item in headings if isinstance(item, str) and item not in text]
    secrets = _report_has_secret(text)
    bench = recovery.get("report_is_bench85") is True
    passed = (
        len(text) >= min_chars
        and not missing
        and (expected.get("relative_links") is not True or relative is True)
        and (expected.get("no_absolute_path") is not True or absolute is False)
        and (expected.get("no_secrets") is not True or secrets is False)
        and int(score) >= int(expected.get("min_score") or 0)
        and (expected.get("not_bench85") is not True or bench is False)
    )
    if passed:
        return _ok(spec, True, "ok")
    return _ok(
        spec,
        False,
        f"报告规则未通过: chars={len(text)} missing={missing} score={score}",
    )


def _tool_called(trace: TraceRecord, spec: HardAssertionSpec) -> AssertionResult:
    """至少调用过指定工具一次；用于证明模型用上了可选工具。"""
    expected = spec.expected
    name: str | None = None
    min_count = 1
    require_ok = False
    if isinstance(expected, str):
        name = expected
    elif isinstance(expected, dict):
        raw_name = expected.get("name")
        name = raw_name if isinstance(raw_name, str) else None
        min_count = int(expected.get("min_count") or 1)
        require_ok = expected.get("require_ok") is True
    if not name:
        return _fail(spec, "tool_called 的 expected 必须是工具名或含 name 的映射")
    matched = [
        item
        for item in trace.tool_calls
        if item.name == name and (not require_ok or item.is_error is False)
    ]
    passed = len(matched) >= min_count
    return _ok(
        spec,
        passed,
        "ok" if passed else f"未调用 {name!r}（count={len(matched)} < {min_count}）",
    )


def _knowledge_hits_variable(trace: TraceRecord, spec: HardAssertionSpec) -> AssertionResult:
    """别名查询必须在融合 Top-K 父段中命中官方变量名；source 必须是相对路径。"""
    expected = spec.expected
    if not isinstance(expected, dict):
        return _fail(spec, "knowledge_hits_variable 的 expected 必须是映射")
    query = expected.get("query")
    variable = expected.get("variable")
    max_rank = int(expected.get("max_rank") or 5)
    candidates = []
    for item in trace.tool_calls:
        if item.name != "climate_query_knowledge":
            continue
        recorded = (item.input_redacted or {}).get("query")
        if recorded is None:
            recorded = (item.output_redacted or {}).get("query")
        if query is not None and recorded != query:
            continue
        candidates.append(item)
    if not candidates:
        label = query if query is not None else "climate_query_knowledge"
        return _ok(spec, False, f"未找到查询 {label!r}")
    last_error: str | None = None
    for matched in candidates:
        if matched.is_error:
            last_error = str(matched.error_code)
            continue
        hits = (matched.output_redacted or {}).get("hits") or []
        if not isinstance(hits, list):
            last_error = "hits 不是列表"
            continue
        texts: list[str] = []
        absolute = False
        for hit in hits[:max_rank]:
            if not isinstance(hit, dict):
                continue
            source = str(hit.get("source") or "")
            if source.startswith("/") or (len(source) >= 3 and source[1:3] in {":/", ":\\"}):
                absolute = True
                break
            texts.append(str(hit.get("parent_text") or ""))
        if absolute:
            return _ok(spec, False, "source 含绝对路径")
        blob = "\n".join(texts)
        if isinstance(variable, str) and variable in blob:
            return _ok(spec, True, "ok")
        last_error = f"Top-{max_rank} 未命中 {variable!r}"
    if last_error and all(item.is_error for item in candidates):
        return _ok(spec, False, f"查询失败: {last_error}")
    return _ok(spec, False, last_error or f"Top-{max_rank} 未命中 {variable!r}")


def _report_has_secret(text: str) -> bool:
    lowered = text.lower()
    if "sk-" in lowered or "cds-token-" in lowered or ".cdsapirc" in lowered:
        return True
    if "api_key=" in lowered or "token=" in lowered:
        return True
    return False


def _artifacts_match(trace: TraceRecord, spec: HardAssertionSpec) -> AssertionResult:
    expected_kinds = spec.expected
    if not isinstance(expected_kinds, list):
        return _fail(spec, "artifacts_match_manifest 的 expected 必须是 kind 列表")
    kinds = [item.get("kind") for item in trace.artifact_manifest if isinstance(item, dict)]
    missing = [kind for kind in expected_kinds if kind not in kinds]
    mismatched = [
        item
        for item in trace.artifact_manifest
        if isinstance(item, dict) and item.get("matches_context") is False
    ]
    passed = not missing and not mismatched and bool(trace.artifact_manifest)
    if passed:
        return _ok(spec, True, "ok")
    return _ok(spec, False, f"artifact 不匹配: missing={missing} mismatched={mismatched}")


def _report_relative_links(trace: TraceRecord, spec: HardAssertionSpec) -> AssertionResult:
    flags = None
    for item in trace.tool_calls:
        if item.name == "climate_write_report":
            flags = item.output_redacted
    recovery = trace.recovery or {}
    relative = (flags or {}).get("has_relative_plot")
    absolute = (flags or {}).get("has_absolute_workspace")
    if relative is None:
        relative = recovery.get("report_has_relative_plot")
        absolute = recovery.get("report_has_absolute_workspace")
    passed = relative is True and absolute is False
    return _ok(spec, passed, "ok" if passed else "报告缺少相对图链接或含绝对 workspace")


def _inspect_profile(trace: TraceRecord, spec: HardAssertionSpec) -> AssertionResult:
    expected = spec.expected
    if not isinstance(expected, dict):
        return _fail(spec, "inspect_profile 的 expected 必须是映射")
    inspect_calls = [item for item in trace.tool_calls if item.name == "climate_inspect_dataset"]
    if not inspect_calls:
        return _fail(spec, "未找到 climate_inspect_dataset 调用")
    profiles = [item.output_redacted for item in inspect_calls]
    first = profiles[0]
    match_first = _subset_equal(first, expected)
    replay_ok = all(_subset_equal(item, expected) for item in profiles)
    passed = match_first and replay_ok
    return _ok(spec, passed, "ok" if passed else f"inspect profile 不匹配: {first}")


def _is_monotonic(versions: list[int], *, mutating_calls: list[Any]) -> bool:
    if not versions:
        return False
    if any(item < 1 for item in versions):
        return False
    if versions != sorted(versions):
        return False
    # 读工具允许 version 不变；写工具成功后不得回退
    prev = None
    for call, version in zip(
        [c for c in mutating_calls if getattr(c, "context_version", None) is not None],
        [c.context_version for c in mutating_calls if getattr(c, "context_version", None) is not None],
        strict=False,
    ):
        del call
        if prev is not None and version < prev:
            return False
        prev = version
    return True


def _subset_equal(actual: Any, expected: Any) -> bool:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False
        return all(key in actual and _subset_equal(actual[key], value) for key, value in expected.items())
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return False
        return all(_subset_equal(a, e) for a, e in zip(actual, expected, strict=True))
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return abs(float(actual) - float(expected)) < 1e-9
    return actual == expected


def _ok(spec: HardAssertionSpec, passed: bool, message: str) -> AssertionResult:
    return AssertionResult(id=spec.id, type=spec.type, passed=passed, message=message)


def _fail(spec: HardAssertionSpec, message: str) -> AssertionResult:
    return AssertionResult(id=spec.id, type=spec.type, passed=False, message=message)
