import concurrent.futures as cf
import datetime as dt
import json
import os
import sys
import urllib.error
import urllib.request

TOKEN = os.environ["GH_TOKEN"]
USER = os.environ.get("GH_USER", "isaacng5")
OUT = os.environ.get("OUT_DIR", "assets")
API = "https://api.github.com"


def req(path, body=None):
    data = json.dumps(body).encode() if body else None
    r = urllib.request.Request(
        API + path,
        data=data,
        headers={"Authorization": f"bearer {TOKEN}", "Accept": "application/vnd.github+json"},
    )
    with urllib.request.urlopen(r, timeout=60) as resp:
        return resp.status, json.loads(resp.read() or "null")


def gql(query, **variables):
    _, d = req("/graphql", {"query": query, "variables": variables})
    if d.get("errors"):
        sys.exit("GraphQL query failed")
    return d["data"]


def repos():
    out, cursor = [], None
    q = """query($c:String){viewer{repositories(first:100,after:$c,isFork:false,
      ownerAffiliations:[OWNER,COLLABORATOR,ORGANIZATION_MEMBER]){
      pageInfo{hasNextPage endCursor} nodes{nameWithOwner}}}}"""
    while True:
        r = gql(q, c=cursor)["viewer"]["repositories"]
        out += [n["nameWithOwner"] for n in r["nodes"]]
        if not r["pageInfo"]["hasNextPage"]:
            return out
        cursor = r["pageInfo"]["endCursor"]


def calendar():
    first = gql("{viewer{createdAt}}")["viewer"]["createdAt"][:4]
    days = {}
    q = """query($f:DateTime,$t:DateTime){viewer{contributionsCollection(from:$f,to:$t){
      contributionCalendar{weeks{contributionDays{date contributionCount}}}}}}"""
    for y in range(int(first), dt.date.today().year + 1):
        c = gql(q, f=f"{y}-01-01T00:00:00Z", t=f"{y}-12-31T23:59:59Z")
        for w in c["viewer"]["contributionsCollection"]["contributionCalendar"]["weeks"]:
            for d in w["contributionDays"]:
                days[d["date"]] = d["contributionCount"]
    today = dt.date.today().isoformat()
    return {k: v for k, v in sorted(days.items()) if k <= today}


CODE_EXT = {
    "py", "ipynb", "r", "rmd", "qmd", "sql", "js", "jsx", "ts", "tsx", "mjs", "cjs", "vue", "svelte",
    "html", "css", "scss", "sh", "zsh", "bash", "swift", "kt", "java", "go", "rs", "c", "cpp", "h",
    "rb", "php", "lua", "do", "jl", "m", "toml", "yml", "yaml", "dockerfile", "tf",
}
SKIP_PARTS = ("node_modules/", "vendor/", "dist/", "build/", ".next/", "site-packages/", "__pycache__/", "venv/")
MAX_FILE_LINES = 3000


def is_code(path, added):
    low = path.lower()
    name = low.rsplit("/", 1)[-1]
    ext = name.rsplit(".", 1)[-1] if "." in name else name
    if ext not in CODE_EXT or ".min." in name or "lock" in name:
        return False
    if any(p in low for p in SKIP_PARTS):
        return False
    return added <= MAX_FILE_LINES


def my_commits(repo):
    shas, page = [], 1
    while True:
        try:
            _, d = req(f"/repos/{repo}/commits?author={USER}&per_page=100&page={page}")
        except urllib.error.HTTPError as e:
            if e.code in (404, 409):
                return shas
            raise SystemExit(f"API error {e.code}; not publishing a partial card")
        if not d:
            return shas
        shas += [c["sha"] for c in d if len(c.get("parents", [])) <= 1]
        page += 1


def commit_files(repo, sha):
    files, page = [], 1
    while True:
        try:
            _, d = req(f"/repos/{repo}/commits/{sha}?per_page=300&page={page}")
        except urllib.error.HTTPError as e:
            if e.code in (404, 409, 422):
                return files
            raise SystemExit(f"API error {e.code}; not publishing a partial card")
        batch = d.get("files") or []
        files += batch
        if len(batch) < 300:
            return files
        page += 1


def repo_totals(repo):
    shas = my_commits(repo)
    with cf.ThreadPoolExecutor(8) as ex:
        changes = [f for fs in ex.map(lambda sha: commit_files(repo, sha), shas) for f in fs]
    sources = {f["filename"].rsplit(".", 1)[0].lower() for f in changes
               if f["filename"].lower().endswith((".qmd", ".rmd", ".ipynb"))}
    added = 0
    for f in changes:
        name = f["filename"]
        if name.lower().endswith(".html") and name.rsplit(".", 1)[0].lower() in sources:
            continue
        if is_code(name, f["additions"]):
            added += f["additions"]
    return len(shas), added


def main():
    skip = {x.strip().lower() for x in os.environ.get("STATS_EXCLUDE_REPOS", "").split(",") if x.strip()}
    commits = lines = 0
    for name in repos():
        if name.lower() in skip:
            continue
        c, a = repo_totals(name)
        commits += c
        lines += a
    stats = {
        "lines": lines,
        "commits": commits,
        "active_days": sum(1 for v in calendar().values() if v),
    }
    print(json.dumps(stats, indent=2))
    os.makedirs(OUT, exist_ok=True)
    for theme in ("light", "dark"):
        with open(f"{OUT}/stats-{theme}.svg", "w") as f:
            f.write(render(stats, theme))


def fmt(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 10_000:
        return f"{n / 1000:.0f}k"
    return f"{n:,}"


THEMES = {
    "light": dict(value="#1f2328", label="#656d76", rule="#d8dee4"),
    "dark": dict(value="#f0f6fc", label="#8d96a0", rule="#30363d"),
}
FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans',Helvetica,Arial,sans-serif"


def render(s, theme):
    t = THEMES[theme]
    tiles = [
        (fmt(s["lines"]), "lines written"),
        (fmt(s["commits"]), "commits"),
        (fmt(s["active_days"]), "active days"),
    ]
    w, h = 720, 92
    cw = w / len(tiles)
    parts = []
    for i, (val, lab) in enumerate(tiles):
        x = i * cw + (0 if i == 0 else 28)
        if i:
            parts.append(f'<line x1="{i * cw:.0f}" y1="18" x2="{i * cw:.0f}" y2="{h - 10}" stroke="{t["rule"]}"/>')
        parts.append(
            f'<text x="{x:.0f}" y="50" class="v">{val}</text>'
            f'<text x="{x:.0f}" y="74" class="l">{lab}</text>'
        )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-label="Lines written, commits and active days">
<style>
.v{{font:600 34px {FONT};fill:{t["value"]};letter-spacing:-0.5px}}
.l{{font:500 12px {FONT};fill:{t["label"]};letter-spacing:0.6px;text-transform:uppercase}}
</style>
{''.join(parts)}
</svg>
"""


if __name__ == "__main__":
    main()
