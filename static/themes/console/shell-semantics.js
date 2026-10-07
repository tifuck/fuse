// shell-semantics presentation. Dependencies are live getters supplied by index.js.
export function create(deps) {
function ccShellSegments(command) {
    const src = String(command || "");
    const out = [];
    let cur = "";
    let quote = "";
    let depth = 0;
    for (let i = 0; i < src.length; i += 1) {
      const ch = src[i];
      if (quote) {
        cur += ch;
        if (ch === "\\" && quote !== "'" && i + 1 < src.length) cur += src[++i];
        else if (ch === quote) quote = "";
        continue;
      }
      if (ch === "\\" && i + 1 < src.length) { cur += ch + src[++i]; continue; }
      if (ch === "'" || ch === '"' || ch === "`") { quote = ch; cur += ch; continue; }
      if (ch === "(") depth += 1;
      else if (ch === ")") depth = Math.max(0, depth - 1);
      if (depth === 0) {
        if (ch === "#" && (!cur.length || /\s/.test(src[i - 1]))) {
          while (i + 1 < src.length && src[i + 1] !== "\n") i += 1;
          continue;
        }
        if (ch === "\n" || ch === ";") { out.push(cur); cur = ""; continue; }
        if (ch === "|") { out.push(cur); cur = ""; if (src[i + 1] === "|" || src[i + 1] === "&") i += 1; continue; }
        if (ch === "&" && src[i - 1] !== ">" && src[i - 1] !== "<" && src[i + 1] !== ">") {
          out.push(cur); cur = "";
          if (src[i + 1] === "&") i += 1;
          continue;
        }
      }
      cur += ch;
    }
    out.push(cur);
    return out.map((part) => part.trim()).filter(Boolean);
  }

function ccShellKind(command) {
    const none = { isSearch: false, isRead: false, isList: false };
    const known = (word) => CC_SEARCH_CMDS.has(word) || CC_READ_CMDS.has(word) || CC_LIST_CMDS.has(word) || CC_NEUTRAL_CMDS.has(word);
    const first = CC_FIRST_CMD.exec(String(command || ""))?.[1];
    if (!first || !known(first)) return none;
    const kind = { isSearch: false, isRead: false, isList: false };
    let any = false;
    for (const segment of ccShellSegments(command)) {
      const word = segment.split(/\s+/)[0];
      if (!word || CC_NEUTRAL_CMDS.has(word)) continue;
      if (!known(word)) return none;
      any = true;
      if (CC_SEARCH_CMDS.has(word)) kind.isSearch = true;
      if (CC_READ_CMDS.has(word)) kind.isRead = true;
      if (CC_LIST_CMDS.has(word)) kind.isList = true;
    }
    return any ? kind : none;
  }

function ccGitRe(sub, tail = "") {
    return new RegExp(`\\bgit(?:\\s+-[cC]\\s+\\S+|\\s+--[^\\s=]+=\\S+)*\\s+${sub}\\b${tail}`);
  }

function ccPrAction(command) {
    const bare = command.replace(CC_QUOTED, " ");
    const gh = CC_GH_PR.find(([re]) => re.test(command))?.[1];
    if (gh === "merged") {
      if (/--disable-auto\b/.test(bare)) return "auto-merge-disabled";
      if (/--auto\b/.test(bare)) return "auto-merge-enabled";
    } else if (gh === "ready" && /--undo\b/.test(bare)) return "draft";
    if (gh) return gh;
    const mr = CC_GLAB_MR.find(([re]) => re.test(command))?.[1];
    if (mr === "edited") {
      if (/--ready\b/.test(bare)) return "ready";
      if (/--draft\b/.test(bare)) return "draft";
    }
    return mr;
  }

function ccRefAfter(command, verb) {
    const rest = command.split(ccGitRe(verb))[1];
    if (!rest) return undefined;
    for (const token of rest.trim().split(/\s+/)) {
      if (/^(?:[\d*]*[<>]|[&|;])/.test(token)) break;
      if (!token.startsWith("-")) return token;
    }
    return undefined;
  }

function ccGitOps(command, output) {
    const ops = {};
    const picked = CC_GIT_PICK.test(command);
    if (CC_GIT_COMMIT.test(command) || picked) {
      const clean = output.replace(/\x1b\[[0-9;]*K/g, "").replace(/\x1b\[[0-9]*G/g, "\n").replace(/\r/g, "\n");
      const hit = clean.match(/^\[(?:([\w./-]+)|detached HEAD)(?: \(root-commit\))? ([0-9a-f]{4,})\]/m);
      if (hit && hit[2] !== undefined) ops.commit = { sha: hit[2], kind: picked ? "cherry-picked" : /--amend\b/.test(command) ? "amended" : "committed" };
    }
    if (CC_GIT_PUSH.test(command)) {
      const args = (command.split(CC_GIT_PUSH)[1] ?? "").split(/[&|;\n]/)[0] ?? "";
      const ref = /(?:^|\s)(?:-n|--dry-run)(?=\s|$)/.test(args) ? null
        : output.match(/^\s*[+\-*!= ]?\s*(?:\[new branch\]|[0-9a-f]+\.\.+[0-9a-f]+)\s+\S+\s*->\s*(\S+)/m)?.[1];
      if (ref) ops.push = ref;
    }
    if (CC_GIT_MERGE.test(command) && /(Fast-forward|Merge made by)/.test(output)) {
      const ref = ccRefAfter(command, "merge");
      if (ref) ops.branch = { ref, action: "merged" };
    }
    if (CC_GIT_REBASE.test(command) && /Successfully rebased/.test(output)) {
      const ref = ccRefAfter(command, "rebase");
      if (ref) ops.branch = { ref, action: "rebased" };
    }
    const action = ccPrAction(command);
    if (action) {
      const urls = output.match(new RegExp(CC_PR_URL.source, "g"));
      const last = urls ? urls[urls.length - 1].match(CC_PR_URL) : null;
      if (last) ops.pr = { number: Number(last[2]), url: last[0], action };
      else if (CC_GH_PR.some(([re]) => re.test(command))) {
        const named = output.match(/[Pp]ull request (?:(\S+)#)?#?(\d{1,9})\b/);
        if (named?.[2]) ops.pr = { number: Number(named[2]), action };
      }
    }
    return Object.keys(ops).length ? ops : null;
  }

function ccToolClass(trace) {
    const name = trace && trace.name;
    if (name === "read_file") return { kind: "read" };
    if (name === "grep" || name === "glob") return { kind: "search" };
    if (name === "list_dir") return { kind: "list" };
    if (name === "search_tools") return { kind: "silent" };
    if (name === "run_terminal_command") {
      // A call that changed files prints on its own row with its diff (Console).
      if ((Array.isArray(trace.edited_files)) && trace.edited_files.length) return null;
      if (trace.platform === "windows") return { kind: "bash" };
      const kind = ccShellKind(deps.displayCommand(trace));
      if (kind.isList) return { kind: "list", shell: true };
      if (kind.isSearch) return { kind: "search", shell: true };
      if (kind.isRead) return { kind: "read", shell: true };
      return { kind: "bash" };
    }
    return null;
  }

function ccExitMeaning(command, code) {
    if (code === null || code === 0) return { isError: false, message: "" };
    const segments = ccShellSegments(command);
    const words = String(segments[segments.length - 1] || command || "").trim().split(/\s+/);
    let note = CC_EXIT_NOTES[words[0]];
    if (words[0] === "git") {
      let sub = "";
      for (let i = 1; i < words.length; i += 1) {
        if (words[i].startsWith("-")) { if (words[i] === "-C" || words[i] === "-c") i += 1; continue; }
        sub = words[i];
        break;
      }
      if (sub === "diff") note = "Files differ";
      if (sub === "grep") note = "No matches found";
    }
    if (note === undefined) return { isError: true, message: "" };
    return { isError: code >= 2, message: code === 1 ? note : "" };
  }

function ccShellWords(text) {
    const words = [];
    let cur = "";
    let quote = "";
    let started = false;
    for (let i = 0; i < text.length; i += 1) {
      const ch = text[i];
      if (quote) {
        if (ch === quote) quote = "";
        else if (quote === '"' && (ch === "$" || ch === "`")) return null;
        else if (quote === '"' && ch === "\\" && i + 1 < text.length) cur += text[++i];
        else cur += ch;
        continue;
      }
      if (ch === "'" || ch === '"') { quote = ch; started = true; continue; }
      if (ch === "\\" && i + 1 < text.length) { cur += text[++i]; started = true; continue; }
      if (/\s/.test(ch)) {
        if (started) words.push(cur);
        cur = "";
        started = false;
        continue;
      }
      if (/[$`|;&<>(){}*?[\]#~]/.test(ch)) return null;
      cur += ch;
      started = true;
    }
    if (quote) return null;
    if (started) words.push(cur);
    return words;
  }

function ccSedEdit(command) {
    const words = ccShellWords(String(command || "").trim());
    if (!words || words[0] !== "sed") return null;
    let inPlace = false;
    let expr = null;
    let file = null;
    for (let i = 1; i < words.length; i += 1) {
      const word = words[i];
      if (word === "-i" || word === "--in-place") {
        inPlace = true;
        const next = words[i + 1];
        if (next !== undefined && !next.startsWith("-") && (next === "" || next.startsWith("."))) i += 1;
        continue;
      }
      if (word.startsWith("-i")) { inPlace = true; continue; }
      if (word === "-E" || word === "-r" || word === "--regexp-extended") continue;
      if (word === "-e" || word === "--expression") {
        if (expr !== null || i + 1 >= words.length) return null;
        expr = words[++i];
        continue;
      }
      if (word.startsWith("--expression=")) {
        if (expr !== null) return null;
        expr = word.slice(13);
        continue;
      }
      if (word.startsWith("-")) return null;
      if (expr === null) expr = word;
      else if (file === null) file = word;
      else return null;
    }
    if (!inPlace || !expr || !file || !expr.startsWith("s/")) return null;
    let part = 0;
    let flags = "";
    for (let i = 2; i < expr.length; i += 1) {
      if (expr[i] === "\\") { i += 1; continue; }
      if (expr[i] === "/") { part += 1; if (part > 2) return null; continue; }
      if (part === 2) flags += expr[i];
    }
    if (part !== 2 || !/^[gpimIM1-9]*$/.test(flags)) return null;
    return { file };
  }

const CC_SEARCH_CMDS = new Set(["find", "grep", "rg", "ag", "ack", "locate", "which", "whereis"]);

const CC_READ_CMDS = new Set(["cat", "head", "tail", "less", "more", "wc", "stat", "file", "strings", "jq", "awk", "cut", "sort", "uniq", "tr"]);

const CC_LIST_CMDS = new Set(["ls", "tree", "du"]);

const CC_NEUTRAL_CMDS = new Set(["echo", "printf", "true", "false", ":"]);

const CC_FIRST_CMD = /^\s*(?:(?:#[^\n]*|\\)\n\s*)*([^\s|&;<>()]+)/;

const CC_GIT_COMMIT = ccGitRe("commit");

const CC_GIT_PUSH = ccGitRe("push");

const CC_GIT_PICK = ccGitRe("cherry-pick");

const CC_GIT_MERGE = ccGitRe("merge", "(?!-)");

const CC_GIT_REBASE = ccGitRe("rebase");

const CC_QUOTED = /"(?:[^"\\]|\\.)*"|'[^']*'/g;

const CC_PR_URL = /https?:\/\/[^/\s"]+\/([^\s"]+?)\/(?:pull|pull-requests|-\/merge_requests)\/(\d+)/;

const CC_GH_PR = [["create", "created"], ["edit", "edited"], ["merge", "merged"], ["comment", "commented"],
    ["close", "closed"], ["reopen", "reopened"], ["ready", "ready"]].map(([sub, action]) => [new RegExp(`\\bgh\\s+pr\\s+${sub}\\b`), action]);

const CC_GLAB_MR = [["create", "created"], ["update", "edited"], ["merge", "merged"], ["note", "commented"],
    ["close", "closed"], ["reopen", "reopened"]].map(([sub, action]) => [new RegExp(`\\bglab\\s+mr\\s+${sub}\\b`), action]);

const CC_EXIT_NOTES = {
    grep: "No matches found", rg: "No matches found", egrep: "No matches found", fgrep: "No matches found",
    find: "Some directories were inaccessible", diff: "Files differ", test: "Condition is false", "[": "Condition is false",
  };

  return { ccShellSegments, ccShellKind, ccGitRe, ccPrAction, ccRefAfter, ccGitOps, ccToolClass, ccExitMeaning, ccShellWords, ccSedEdit, CC_SEARCH_CMDS, CC_READ_CMDS, CC_LIST_CMDS, CC_NEUTRAL_CMDS, CC_FIRST_CMD, CC_GIT_COMMIT, CC_GIT_PUSH, CC_GIT_PICK, CC_GIT_MERGE, CC_GIT_REBASE, CC_QUOTED, CC_PR_URL, CC_GH_PR, CC_GLAB_MR, CC_EXIT_NOTES };
}
