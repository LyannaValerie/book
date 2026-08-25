"""Complete Book V1 command-line surface."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from . import CASE_SCHEMA_VERSION, CONTENT_TRUST, TOOL_VERSION
from .authoring import create_case, import_case, minimal_interactive
from .core import BookError, atomic_write, loads_json, read_json, utc_now
from .events import append_event
from .index import reindex
from .ladders import add_ladder, list_ladders, show_ladder
from .maintenance import doctor, gc_candidates, migrate_v0, verify
from .paths import explore, mark_path, search_paths
from .references import resolve
from .relations import add_relation, references
from .retention import assess as retention_assess, record as retention_record
from .search import search_cases
from .tasks import add_missing, add_state, begin, finish, load_task, note_loaded, receipt, set_next_probe, validate as validate_task
from .views import list_view, set_facets, show_item
from . import v0


def emit(value: Any) -> None:
    json.dump(value, sys.stdout, ensure_ascii=False, sort_keys=True, indent=2); sys.stdout.write("\n")


def payload_from(args: argparse.Namespace, *, interactive=None) -> Any:
    sources = sum(bool(item) for item in (getattr(args, "stdin", False), getattr(args, "json_payload", None), getattr(args, "input", None)))
    if sources > 1: raise BookError("INPUT_AMBIGUOUS", "choose exactly one input source")
    if getattr(args, "stdin", False): return loads_json(sys.stdin.read(), "stdin")
    if getattr(args, "json_payload", None): return loads_json(args.json_payload, "--json")
    if getattr(args, "input", None): return read_json(args.input)
    if interactive is not None:
        if not sys.stdin.isatty(): raise BookError("TTY_REQUIRED", "interactive add-case requires a TTY; use --stdin or --json")
        return interactive()
    raise BookError("INPUT_REQUIRED", "structured input is required")


def add_payload_options(parser: argparse.ArgumentParser, *, positional: bool = False) -> None:
    if positional: parser.add_argument("input", type=Path, nargs="?")
    parser.add_argument("--stdin", action="store_true"); parser.add_argument("--json", dest="json_payload")


def stage_payload(root: Path, prefix: str, payload: Any) -> Path:
    directory = root / ".book" / "staging"; directory.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f"{prefix}-", suffix=".json", dir=directory); os.close(fd)
    path = Path(name); atomic_write(path, payload); return path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="book", description="Book V1 operational memory and navigation")
    parser.add_argument("--book", type=Path, default=Path(__file__).resolve().parents[1], help="Book root")
    parser.add_argument("--task", help="associate observable access with a Task ID")
    parser.add_argument("--actor", default=os.environ.get("USER", "agent")); parser.add_argument("--version", action="store_true")
    commands = parser.add_subparsers(dest="command")
    search = commands.add_parser("search"); search.add_argument("query"); search.add_argument("--within"); search.add_argument("--limit", type=int, default=50)
    listing = commands.add_parser("list"); listing.add_argument("view", nargs="?"); listing.add_argument("--limit", type=int, default=100)
    show = commands.add_parser("show"); show.add_argument("identifier"); show.add_argument("--metadata", action="store_true")
    add = commands.add_parser("add-case"); add_payload_options(add, positional=True); add.add_argument("--allow-similar", action="store_true")
    imp = commands.add_parser("import-case"); imp.add_argument("input", type=Path); imp.add_argument("--allow-similar", action="store_true")
    revise = commands.add_parser("revise"); revise.add_argument("id"); revise.add_argument("--if-revision", required=True); revise.add_argument("--patch", type=Path); revise.add_argument("--stdin", action="store_true"); revise.add_argument("--json", dest="json_payload"); revise.add_argument("--updated-at"); revise.add_argument("--updated-by"); revise.add_argument("--reason", default="semantic revision")
    challenge = commands.add_parser("challenge"); challenge.add_argument("id"); challenge.add_argument("--if-revision", required=True); challenge.add_argument("--challenge", type=Path); challenge.add_argument("--stdin", action="store_true"); challenge.add_argument("--json", dest="json_payload"); challenge.add_argument("--updated-at"); challenge.add_argument("--updated-by"); challenge.add_argument("--reason", default="contrary evidence recorded")
    facet = commands.add_parser("facet"); fs = facet.add_subparsers(dest="facet_command", required=True); fset = fs.add_parser("set"); fset.add_argument("id"); fset.add_argument("--domain", required=True); fset.add_argument("--view", action="append", default=[]); fset.add_argument("--synthetic", action="store_true")
    relate = commands.add_parser("relate"); relate.add_argument("source"); relate.add_argument("type"); relate.add_argument("target"); relate.add_argument("--epistemic", choices=sorted(__import__('booklib.relations',fromlist=['EPISTEMIC_CLASSES']).EPISTEMIC_CLASSES), default="ASSERTED"); relate.add_argument("--oracle")
    refs = commands.add_parser("references"); refs.add_argument("id"); refs.add_argument("--depth", type=int, default=1)
    resolver = commands.add_parser("resolve"); resolver.add_argument("reference")
    task = commands.add_parser("task"); ts = task.add_subparsers(dest="task_command", required=True)
    tb = ts.add_parser("begin"); tb.add_argument("--goal", required=True); tb.add_argument("--project", required=True); tb.add_argument("--domain"); tb.add_argument("--id"); tb.add_argument("--external-task", dest="external_task")
    for name in ("status", "receipt"): sub = ts.add_parser(name); sub.add_argument("id")
    tm = ts.add_parser("missing"); tm.add_argument("id"); tm.add_argument("question")
    for name in ("known", "hypothesis"):
        state = ts.add_parser(name); state.add_argument("id"); state.add_argument("statement")
    tn = ts.add_parser("next-probe"); tn.add_argument("id"); tn.add_argument("--action", required=True); tn.add_argument("--observable", required=True); tn.add_argument("--oracle", required=True); tn.add_argument("--authorized", action="store_true"); tn.add_argument("--bounded", action="store_true"); tn.add_argument("--discriminative", action="store_true"); tn.add_argument("--no-high-risk-gap", action="store_true")
    tv = ts.add_parser("validate"); tv.add_argument("id"); tv.add_argument("--oracle", required=True); tv.add_argument("--outcome", required=True); tv.add_argument("--passed", action="store_true"); tv.add_argument("--summary", default="")
    tf = ts.add_parser("finish"); tf.add_argument("id"); tf.add_argument("--outcome", required=True)
    event = commands.add_parser("event"); es = event.add_subparsers(dest="event_command", required=True); ea = es.add_parser("add"); ea.add_argument("--kind", required=True); ea.add_argument("--summary", required=True); ea.add_argument("--task"); ea.add_argument("--event-key"); ea.add_argument("--source-reads", type=int, default=0); ea.add_argument("--tool-calls", type=int, default=0); ea.add_argument("--full-file-reads", type=int, default=0); ea.add_argument("--wall-time", type=float); ea.add_argument("--reverts", type=int, default=0)
    use = commands.add_parser("use"); use.add_argument("id"); use.add_argument("--as", dest="utility", choices=("support", "resolved"), required=True); use.add_argument("--task", required=True)
    retention = commands.add_parser("retention"); rs = retention.add_subparsers(dest="retention_command", required=True); ra = rs.add_parser("assess"); add_payload_options(ra); rr = rs.add_parser("record"); rr.add_argument("--task", required=True); rr.add_argument("--decision", choices=("new","revise","challenge","no-op"), required=True); rr.add_argument("--summary", required=True); rr.add_argument("--case")
    path = commands.add_parser("path"); ps = path.add_subparsers(dest="path_command", required=True); psearch = ps.add_parser("search"); psearch.add_argument("query", nargs="?", default=""); psearch.add_argument("--success", nargs="?", const=""); psearch.add_argument("--domain"); psearch.add_argument("--limit",type=int,default=50); pm = ps.add_parser("mark"); pm.add_argument("id"); pm.add_argument("--status", required=True); pm.add_argument("--reason", required=True); pm.add_argument("--evidence"); pe = ps.add_parser("explore"); pe.add_argument("--primary", required=True); pe.add_argument("--candidate", required=True); pe.add_argument("--budget", type=int, required=True)
    ladder = commands.add_parser("ladder"); ls = ladder.add_subparsers(dest="ladder_command", required=True); la = ls.add_parser("add"); add_payload_options(la); ls.add_parser("list"); lshow = ls.add_parser("show"); lshow.add_argument("id")
    commands.add_parser("verify"); commands.add_parser("doctor"); commands.add_parser("reindex"); commands.add_parser("migrate-v0")
    gc = commands.add_parser("gc"); gcs = gc.add_subparsers(dest="gc_command", required=True); gcs.add_parser("candidates")
    return parser


def _record_access(root: Path, task_id: str | None, kind: str, result: Any, data: dict[str, Any], summary: str) -> None:
    if not task_id: return
    payload = json.dumps(result, ensure_ascii=False, sort_keys=True); data = {**data, "served_bytes": len(payload.encode()), "served_chars": len(payload)}
    append_event(root, kind, task_id=task_id, summary=summary, data=data)


def dispatch(args: argparse.Namespace) -> tuple[Any, int]:
    root = args.book
    if args.command == "search":
        result = search_cases(root, args.query, args.within,limit=max(1,min(args.limit,500))); _record_access(root,args.task,"SEARCH",result,{"query":args.query,"result_count":result["count"]},args.query); note_loaded(root,args.task,[item["id"] for item in result["results"]]); return result,0
    if args.command == "list":
        result=list_view(root,args.view,limit=max(1,min(args.limit,500))); _record_access(root,args.task,"LIST",result,{"view":args.view or "/","result_count":result["count"]},args.view or "/"); return result,0
    if args.command == "show":
        result=show_item(root,args.identifier,metadata_only=args.metadata); _record_access(root,args.task,"SHOW",result,{"id":args.identifier},args.identifier); note_loaded(root,args.task,[args.identifier]); return result,0
    if args.command == "add-case":
        if args.input and not args.stdin and not args.json_payload: result=v0.add_case(root,args.input,args.allow_similar)
        else: result=create_case(root,payload_from(args,interactive=minimal_interactive),actor=args.actor,allow_similar=args.allow_similar)
        append_event(root,"CASE_ADD",task_id=args.task,summary=result["id"],data={"case_id":result["id"]}); return result,0
    if args.command == "import-case":
        result=import_case(root,args.input,allow_similar=args.allow_similar); append_event(root,"CASE_ADD",task_id=args.task,summary=result["id"],data={"case_id":result["id"]}); return result,0
    if args.command == "revise":
        temporary = None
        if args.patch: patch_path=args.patch
        else: temporary=stage_payload(root,"revision",payload_from(args)); patch_path=temporary
        try: result=v0.revise_case(root,args.id,args.if_revision,patch_path,args.updated_at or utc_now(),args.updated_by or args.actor,args.reason)
        finally:
            if temporary: temporary.unlink(missing_ok=True)
        append_event(root,"CASE_REVISE",task_id=args.task,summary=args.id,data={"case_id":args.id}); return result,0
    if args.command == "challenge":
        if args.challenge: challenge_path=args.challenge
        else:
            semantic=payload_from(args)
            if not isinstance(semantic,dict) or not {"statement","evidence"} <= semantic.keys(): raise BookError("SCHEMA_INVALID","semantic challenge requires statement and evidence")
            statement=semantic["statement"] if isinstance(semantic["statement"],dict) else {"class":"ASSERTED","text":semantic["statement"]}
            challenge={"id":"C-"+__import__('secrets').token_hex(5).upper(),"observed_at":semantic.get("observed_at"),"reported_by":args.updated_by or args.actor,"statement":statement,"evidence":semantic["evidence"],"references":semantic.get("references",[])}
            challenge_path=stage_payload(root,"challenge",challenge); temporary=challenge_path
        if args.challenge: temporary=None
        try: result=v0.challenge_case(root,args.id,args.if_revision,challenge_path,args.updated_at or utc_now(),args.updated_by or args.actor,args.reason)
        finally:
            if temporary: temporary.unlink(missing_ok=True)
        append_event(root,"CASE_CHALLENGE",task_id=args.task,summary=args.id,data={"case_id":args.id}); return result,0
    if args.command == "facet": return {"ok":True,"operation":"facet set","facets":set_facets(root,args.id,args.domain,args.view,synthetic=args.synthetic)},0
    if args.command == "relate":
        result=add_relation(root,args.source,args.type,args.target,args.epistemic,args.oracle,args.actor); append_event(root,"RELATION_ADD",task_id=args.task,summary=result["relation"]["id"],data={"relation_id":result["relation"]["id"]}); return result,0
    if args.command == "references":
        result=references(root,args.id,depth=args.depth); _record_access(root,args.task,"REFERENCE_FOLLOW",result,{"reference":args.id,"result_count":len(result["incoming"])+len(result["outgoing"])},args.id); note_loaded(root,args.task,[e["to"] for e in result["outgoing"]],followed=True); return result,0
    if args.command == "resolve":
        result=resolve(root,args.reference); _record_access(root,args.task,"REFERENCE_FOLLOW",result,{"reference":args.reference},args.reference); note_loaded(root,args.task,[args.reference],followed=True); return result,0
    if args.command == "task":
        if args.task_command=="begin": return begin(root,args.goal,args.project,domain=args.domain,task_id=args.id,external_task_ref=args.external_task),0
        if args.task_command=="status": return {"ok":True,"operation":"task status","task":load_task(root,args.id)},0
        if args.task_command=="missing": return add_missing(root,args.id,args.question),0
        if args.task_command=="known": return add_state(root,args.id,"known",args.statement),0
        if args.task_command=="hypothesis": return add_state(root,args.id,"hypotheses",args.statement),0
        if args.task_command=="next-probe": return set_next_probe(root,args.id,args.action,args.observable,args.oracle,authorized=args.authorized,bounded=args.bounded,discriminative=args.discriminative,no_high_risk_gap=args.no_high_risk_gap),0
        if args.task_command=="validate": return validate_task(root,args.id,args.oracle,args.outcome,passed=args.passed,summary=args.summary),0
        if args.task_command=="finish": return finish(root,args.id,args.outcome),0
        return receipt(root,args.id),0
    if args.command == "event":
        kind=args.kind.upper().replace("-","_"); data={"source_reads":args.source_reads,"tool_calls":args.tool_calls,"full_file_reads":args.full_file_reads,"reverts":args.reverts};
        if args.wall_time is not None:data["wall_time"]=args.wall_time
        return append_event(root,kind,task_id=args.task,summary=args.summary,data=data,idempotency_key=args.event_key),0
    if args.command == "use": return append_event(root,"UTILITY",task_id=args.task,summary=args.id,data={"id":args.id,"utility":args.utility}),0
    if args.command == "retention":
        if args.retention_command=="assess": return retention_assess(root,payload_from(args)),0
        return retention_record(root,args.task,args.decision,args.summary,args.case),0
    if args.command == "path":
        if args.path_command=="search":
            result=search_paths(root,args.success if args.success is not None else args.query,success_only=args.success is not None,domain=args.domain,limit=max(1,min(args.limit,500)))
            if args.task: append_event(root,"PATH_SEARCH",task_id=args.task,summary=args.success if args.success is not None else args.query,data={"query":args.success if args.success is not None else args.query,"result_count":result["count"]})
            return result,0
        if args.path_command=="mark": return mark_path(root,args.id,args.status,args.reason,args.task,args.evidence),0
        return explore(root,args.primary,args.candidate,args.budget,args.task),0
    if args.command == "ladder":
        if args.ladder_command=="add": return add_ladder(root,payload_from(args),args.actor),0
        if args.ladder_command=="list": return list_ladders(root),0
        return show_ladder(root,args.id),0
    if args.command == "verify": return verify(root)
    if args.command == "doctor": return doctor(root),0
    if args.command == "reindex": return reindex(root),0
    if args.command == "migrate-v0": return migrate_v0(root),0
    if args.command == "gc": return gc_candidates(root),0
    raise BookError("COMMAND_REQUIRED","a command is required")


def main(argv: list[str] | None = None) -> int:
    parser=build_parser(); args=parser.parse_args(argv)
    if args.version: emit({"tool_version":TOOL_VERSION,"schema_version":CASE_SCHEMA_VERSION,"case_schema_version":CASE_SCHEMA_VERSION,"content_trust":CONTENT_TRUST}); return 0
    if not args.command: parser.error("a command is required")
    try:
        result,status=dispatch(args); emit(result); return status
    except (BookError,v0.BookError) as exc:
        error={"ok":False,"error":exc.code,"message":exc.message}
        if exc.details is not None:error["details"]=exc.details
        emit(error); return 2
