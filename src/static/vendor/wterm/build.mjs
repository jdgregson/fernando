import { build } from 'esbuild';
import { readFile } from 'node:fs/promises';

await build({
    entryPoints: ['entry.js'],
    bundle: true,
    minify: true,
    format: 'iife',
    legalComments: 'none',
    outfile: 'wterm.bundle.js',
    plugins: [{
        name: 'host-scroll-extent',
        setup(builder) {
            builder.onLoad({filter: /[/\\]@wterm[/\\]dom[/\\]dist[/\\]wterm\.js$/}, async ({path}) => {
                let contents = await readFile(path, 'utf8');
                const changes = [
                    ['this.onData = options.onData || null;', 'this.onData = options.onData || null;\n        this.onInputScroll = options.onInputScroll || null;\n        this.onRender = options.onRender || null;'],
                    ['this._outputAnnouncements.input();\n                    this._scrollToBottom();', 'this._outputAnnouncements.input();\n                    if (!this.onInputScroll?.()) this._scrollToBottom();'],
                    ['this._historySelection.clear();\n                this._scrollToBottom();\n            }, (data)', 'this._historySelection.clear();\n                if (!this.onInputScroll?.()) this._scrollToBottom();\n            }, (data)'],
                    ['this._historySelection.clear();\n                this._scrollToBottom();\n                if (this.onBinary)', 'this._historySelection.clear();\n                if (!this.onInputScroll?.()) this._scrollToBottom();\n                if (this.onBinary)'],
                    ['this.input?.syncInputPosition();', 'this.onRender?.();\n        this.input?.syncInputPosition();'],
                    ['if (this.renderer?.hasImageFlow) {\n            frame.maxScrollTop', '{\n            frame.maxScrollTop'],
                    ['else if (!frame.scrollbackCount && !this.renderer?.hasImageFlow) {', 'else if (!frame.scrollbackCount && !this.renderer?.hasImageFlow && frame.maxScrollTop === 0) {'],
                ];
                for (const [before, after] of changes) {
                    if (contents.split(before).length !== 2) throw new Error(`Unexpected wterm source: ${before}`);
                    contents = contents.replace(before, after);
                }
                return {contents, loader: 'js'};
            });
        },
    }],
});

await build({
    entryPoints: ['node_modules/@wterm/dom/src/terminal.css'],
    minify: true,
    legalComments: 'none',
    outfile: 'wterm.css',
});
