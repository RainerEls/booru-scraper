FROM python:3.14-slim AS builder

WORKDIR /app

RUN python3 -m venv /venv
ENV PATH="/venv/bin:$PATH"

RUN --mount=type=cache,target=/root/.cache/pip \
    --mount=type=bind,source=requirements.txt,target=requirements.txt \
    pip install -r requirements.txt

FROM python:3.14-slim

WORKDIR /app

COPY --from=builder /venv /venv
ENV PATH="/venv/bin:$PATH"

COPY . .

EXPOSE 8000

CMD ["/venv/bin/python3", "-m", "uvicorn", "app.main:app", "--host=0.0.0.0", "--port=8000"]