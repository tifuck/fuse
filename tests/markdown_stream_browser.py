"""Exercise the actual renderer and DOM patcher, without models or the service."""
from pathlib import Path
from playwright.sync_api import sync_playwright


def main():
    source = (Path(__file__).resolve().parents[1] / "static/chat.js").read_text()
    code = source[source.index("  function escapeHtml("):source.index("  function splitThink(")]
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox"])
        page = browser.new_page()
        page.set_content('<main id="sample"></main>')
        page.add_script_tag(content=code)
        result = page.evaluate(r"""() => {
          const host = document.querySelector('#sample');
          const source = 'Supports `<details>` and `<summary>`.\n\nKEEP ABOVE\n\n<details>\n<summary>Dump</summary>\n```json\n{"ok":true}\n```\n</details>\n\nAFTER';
          for (let i=1; i<=source.length; i++) {
            patchMarkdown(host, renderMarkdown(source.slice(0,i), true, []));
            if (i >= source.indexOf('KEEP ABOVE') + 10) {
              const prose = [...host.children].filter(e => e.tagName !== 'DETAILS').map(e=>e.textContent).join(' ');
              if (!prose.includes('KEEP ABOVE')) throw Error('Earlier prose disappeared at ' + i);
            }
          }
          const d = host.querySelector('details');
          if (host.querySelectorAll('details').length !== 1 || !d.querySelector('pre')) throw Error('Broken disclosure');
          d.open = true;
          patchMarkdown(host, renderMarkdown(source + ' more', true, []));
          if (!d.open) throw Error('Stream closed user-opened disclosure');
          return 'PASS: every streamed prefix preserves prose; code and disclosure state survive DOM patching';
        }""")
        print(result)
        browser.close()


if __name__ == "__main__":
    main()
