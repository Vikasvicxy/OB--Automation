# Open Source Research

## Code Originality

All code in this project is **original work**. No code was copied from any open source project.

## Patterns Studied

During design, the following open source projects were reviewed for **workflow patterns and architectural ideas only**. No source code was copied, forked, or adapted from these projects.

### Horilla HRMS

- **What was studied**: General HR workflow patterns — how HRMS systems handle candidate pipelines, onboarding flows, and batch processing.
- **What was used**: Conceptual understanding of HR onboarding workflows.
- **What was NOT used**: No code, no database schemas, no UI templates, no API patterns.

### Frappe HR

- **What was studied**: Pipeline/Kanban view patterns — how HR tools visualize candidate status progression through stages.
- **What was used**: Conceptual understanding of Kanban-style candidate tracking.
- **What was NOT used**: No code, no Frappe framework dependencies, no DocType patterns.

### FastAPI/Jinja Patterns

- **What was studied**: Standard web application patterns using FastAPI with Jinja2 server-side rendering.
- **What was used**: Standard FastAPI routing patterns, Jinja2 template rendering, static file serving — all well-documented patterns that are common to any FastAPI application.
- **What was NOT used**: No specific project code was copied. The routing structure, database layer, and business logic are all original.

## Dependencies

All dependencies are listed in `requirements.txt` with their licenses:

| Package | Version | License |
|---------|---------|---------|
| fastapi | 0.115.12 | MIT |
| uvicorn | 0.34.3 | BSD-3-Clause |
| jinja2 | 3.1.6 | BSD-3-Clause |
| python-multipart | 0.0.20 | BSD-3-Clause |
| rapidocr-onnxruntime | 1.2.3 | Apache-2.0 |
| pymupdf | 1.28.2 | AGPL-3.0 |
| Pillow | 12.3.0 | MIT-CMU |
| openpyxl | 3.1.5 | MIT |
| rapidfuzz | 3.14.6 | MIT |
| playwright | 1.62.0 | Apache-2.0 |

### Transitive Dependencies (notable)

- **ONNX Runtime** (via rapidocr-onnxruntime): MIT License
- **NumPy** (via rapidocr-onnxruntime, Pillow): BSD-3-Clause
- **Starlette** (via fastapi): BSD-3-Clause
- **Pydantic** (via fastapi): MIT License
- **Click** (via playwright): BSD-3-Clause

## License Compliance

All dependencies use permissive open source licenses (MIT, BSD, Apache-2.0). The only copyleft dependency is PyMuPDF (AGPL-3.0), which is used as a runtime library (not linked into a distributed binary), so AGPL obligations do not apply to this application.
