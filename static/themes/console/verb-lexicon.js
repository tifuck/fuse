// verb-lexicon presentation. Dependencies are live getters supplied by index.js.
export function create(deps) {
const CC_PAST = {
    begin: "began", bind: "bound", bring: "brought", build: "built", buy: "bought", catch: "caught", choose: "chose",
    come: "came", cut: "cut", debug: "debugged", dig: "dug", do: "did", draw: "drew", feed: "fed", feel: "felt",
    fight: "fought", find: "found", fly: "flew", forget: "forgot", freeze: "froze", get: "got", give: "gave", go: "went",
    have: "had", hide: "hid", hit: "hit", hold: "held", input: "input", keep: "kept", know: "knew", lead: "led",
    leave: "left", let: "let", lose: "lost", make: "made", mean: "meant", meet: "met", override: "overrode",
    overwrite: "overwrote", pay: "paid", put: "put", quit: "quit", read: "read", rebuild: "rebuilt", redo: "redid",
    rerun: "reran", reset: "reset", rewrite: "rewrote", run: "ran", see: "saw", seek: "sought", send: "sent", set: "set",
    show: "showed", shut: "shut", sit: "sat", sleep: "slept", spend: "spent", spin: "spun", split: "split",
    spread: "spread", stand: "stood", sweep: "swept", sync: "synced", take: "took", teach: "taught", tear: "tore",
    tell: "told", think: "thought", throw: "threw", understand: "understood", undo: "undid", unset: "unset", win: "won",
    unwrap: "unwrapped", unzip: "unzipped", write: "wrote",
  };

const CC_ING = {
    begin: "beginning", commit: "committing", control: "controlling", debug: "debugging", emit: "emitting",
    equip: "equipping", forget: "forgetting", format: "formatting", input: "inputting", occur: "occurring",
    omit: "omitting", output: "outputting", permit: "permitting", prefer: "preferring", quit: "quitting",
    refer: "referring", rerun: "rerunning", reset: "resetting", screenshot: "screenshotting", snapshot: "snapshotting",
    submit: "submitting", sync: "syncing", transfer: "transferring", unset: "unsetting", unwrap: "unwrapping",
    unzip: "unzipping",
  };

const CC_PAST_FORMS = new Set([
    ...Object.entries(CC_PAST).filter(([verb, past]) => verb !== past).map(([, past]) => past),
    "been", "broken", "chosen", "done", "drawn", "driven", "eaten", "fallen", "forgotten", "given", "gone", "grown",
    "hidden", "known", "ridden", "risen", "seen", "shown", "spoken", "taken", "thrown", "torn", "worn", "written",
  ]);

const CC_NOUNLIKE = new Set(["output", "input", "lead", "feed", "spread", "set", "cut", "split", "hit", "let", "put",
    "quit", "shut", "read", "go", "make", "dig", "tear", "win", "fly", "spin", "control", "permit", "have", "hold", "keep",
    "mean", "feel", "do", "see", "know", "think", "understand"]);

const CC_VERBS = new Set([
    ...Object.keys(CC_PAST).filter((verb) => !CC_NOUNLIKE.has(verb)),
    ...Object.keys(CC_ING).filter((verb) => !CC_NOUNLIKE.has(verb)),
    "add", "analyze", "append", "apply", "archive", "assert", "attempt", "autofix", "await", "benchmark", "bisect", "call",
    "capture", "check", "clone", "collect", "compare", "compile", "compute", "confirm", "connect", "convert", "copy",
    "count", "create", "curl", "decode", "delete", "deploy", "detect", "discard", "dismiss", "display", "download", "dump",
    "edit", "emit", "enable", "encode", "ensure", "enumerate", "evaluate", "execute", "expand", "expect", "export",
    "extract", "fetch", "fill", "filter", "fix", "flush", "focus", "follow", "generate", "grep", "identify", "ignore",
    "import", "include", "inject", "insert", "inspect", "install", "invoke", "kill", "launch", "lint", "list", "load",
    "locate", "loop", "measure", "merge", "monitor", "move", "navigate", "normalize", "parse", "patch", "pick", "ping",
    "pipe", "poll", "post", "prepare", "preview", "print", "probe", "profile", "prune", "publish", "pull", "push",
    "queue", "rebase", "recheck", "record", "recover", "redirect", "reduce", "refresh", "regenerate", "reinstall",
    "relaunch", "reload", "remove", "rename", "render", "reopen", "repeat", "replay", "reply", "report", "request",
    "resolve", "restart", "restore", "retry", "revert", "save", "scan", "screenshot", "scroll", "search", "select",
    "serialize", "serve", "settle", "skip", "snapshot", "sort", "spawn", "squash", "stage", "start", "stash", "stop",
    "strip", "summarize", "switch", "sync", "tail", "tally", "test", "toggle", "touch", "trace", "track", "trigger",
    "trim", "truncate", "try", "typecheck", "unblock", "uninstall", "unlink", "unmount", "unpack", "update", "upgrade",
    "upload", "validate", "verify", "visit", "wait", "walk", "warn", "wipe",
  ]);

const CC_PREFIXED = /^(re|un|de|pre|co|sub|over|out|mis|auto|post)-(.+)$/i;

const CC_CVC = /[^aeiou][aeiou][bcdfghjklmnpqrstvz]$/i;

const CC_CHAIN_STOP = /^(?:\s+\w+){0,2}\s+(?:is|are|was|were|has|have|do|does|did|will|would|should|can|could|passes|passed|fails|failed|exists|works|worked|succeeds|succeeded|stays|remains|looks|runs|ran|not|if|when|unless|whether)(?:n['’]t)?\b/;

const CC_CHAIN = (() => {
    const tail = "([a-z]{2,16})(?=$|[\\s,;!?]|\\.(?:\\s|$))";
    try {
      return { re: new RegExp(`(?:(?<![\\w-])((?:(?:[Aa]nd|[Tt]hen)\\s+)+)|(,\\s+(?:(?:and|then)\\s+)*))${tail}`, "g"), lookbehind: true };
    } catch (_) {
      return { re: new RegExp(`(?:(^|[^\\w-])((?:(?:[Aa]nd|[Tt]hen)\\s+)+)|(,\\s+(?:(?:and|then)\\s+)*))${tail}`, "g"), lookbehind: false };
    }
  })();

  return { CC_PAST, CC_ING, CC_PAST_FORMS, CC_NOUNLIKE, CC_VERBS, CC_PREFIXED, CC_CVC, CC_CHAIN_STOP, CC_CHAIN };
}
