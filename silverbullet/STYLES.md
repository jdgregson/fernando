```space-style
#sb-main .cm-editor .cm-line {
    padding: 0 10px;
}

.cm-line:not(.sb-line-fenced-code) + .cm-line.sb-line-code-outside.sb-line-fenced-code {
    border-top-right-radius: 4px;
    border-top-left-radius: 4px;
}

.cm-line.sb-line-fenced-code:not(.sb-line-code-outside) + .cm-line.sb-line-code-outside.sb-line-fenced-code {
    border-bottom-right-radius: 4px;
    border-bottom-left-radius: 4px;
}

#sb-main .cm-editor .sb-code-copy-button {
    margin: 3px -7px 0 0;
}

#sb-main .cm-editor .sb-line-code-outside .sb-code-info {
    margin-top: 6px;
    padding-right: 0;
}

.cm-line.sb-line-fenced-code::selection,
.cm-line.sb-line-fenced-code *::selection {
    background-color: #264f78 !important;
}

#sb-main .cm-editor .sb-line-h1, #sb-main .cm-editor h1 {
    font-size: 1.35em;
}

#sb-main .cm-editor .sb-line-h2, #sb-main .cm-editor h2 {
    font-size: 1.25em;
}

```
