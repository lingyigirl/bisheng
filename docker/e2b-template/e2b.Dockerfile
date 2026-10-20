# BiSheng Linsight sandbox template (E2B, 2026-10-16).
#
# Base = E2B's official code-interpreter template: Debian 13, Python 3.13
# (pandas / matplotlib / python-docx / openpyxl / PIL / numpy), Node 20, and
# the full Noto CJK font set. Everything below closes ONLY the gaps measured
# by .venv/f074-tools/f074-e2b-template-probe.py on 2026-10-16:
#
#   missing system tools : soffice, pdftoppm, pandoc, qpdf  (every docx/pptx
#                          conversion used to apt-get install these per task)
#   missing Python libs  : python-pptx, reportlab, pdfplumber, pypdf, xlsxwriter
#   missing global npm   : docx, pptxgenjs, exceljs (the docx/pptx skills'
#                          JS generation route)
#
# Rebuild with:
#   $env:E2B_API_KEY='...'; e2b template create bisheng-linsight-v1 -p docker/e2b-template `
#       --cpu-count 2 --memory-mb 2048 -c "sudo systemctl start jupyter"
# Wire-up: tools group "代码执行器" extra -> config.e2b.template = "bisheng-linsight-v1".
#
# The `-c` flag is REQUIRED: the official code-interpreter template starts its
# Jupyter server (the 49999 endpoint the SDK talks to) via systemd. Rebuilding
# without it produced "The sandbox is running but port is not open (49999)"
# on every run() — measured 2026-10-16.
#
# NOTE: base reference must be the Docker Hub image — `FROM code-interpreter-v1`
# (the E2B template name) fails with "image not found in the registry".
FROM e2bdev/code-interpreter:latest

RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-writer libreoffice-impress libreoffice-calc \
        poppler-utils \
        pandoc \
        qpdf \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir \
        python-pptx \
        reportlab \
        pdfplumber \
        pypdf \
        xlsxwriter

RUN npm install -g docx pptxgenjs exceljs

# Node never searches the global node_modules on its own, and E2B does NOT
# keep Dockerfile ENV in the running sandbox (measured 2026-10-16: $NODE_PATH
# came back empty), so link the three packages into node's guaranteed
# last-resort lookup path instead — `require('docx')` then resolves from any
# cwd, including the jupyter kernel the tool actually executes in.
RUN mkdir -p /node_modules \
    && ln -s /usr/lib/node_modules/docx /node_modules/docx \
    && ln -s /usr/lib/node_modules/pptxgenjs /node_modules/pptxgenjs \
    && ln -s /usr/lib/node_modules/exceljs /node_modules/exceljs
