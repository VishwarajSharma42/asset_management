"""One error envelope for every failure. Routes never build error JSON by hand."""
from __future__ import annotations

import logging
import uuid

from flask import Flask, g, jsonify
from werkzeug.exceptions import HTTPException

log = logging.getLogger("dsoreg")


class AppError(Exception):
    status = 500
    code = "internal_error"
    message = "Something went wrong."

    def __init__(self, message: str | None = None, *, code: str | None = None,
                 details: dict | None = None, retry_after: int | None = None):
        super().__init__(message or self.message)
        if message:
            self.message = message
        if code:
            self.code = code
        self.details = details
        self.retry_after = retry_after


class BadRequest(AppError):
    status, code, message = 400, "bad_request", "The request is not valid."


class Unauthorized(AppError):
    status, code, message = 401, "unauthorized", "Authentication is required."


class Forbidden(AppError):
    status, code, message = 403, "forbidden", "You are not allowed to do that."


class NotFound(AppError):
    status, code, message = 404, "not_found", "Not found."


class Conflict(AppError):
    status, code, message = 409, "conflict", "That conflicts with the current state."


class PreconditionRequired(AppError):
    status, code, message = 428, "precondition_required", "If-Match is required."


class PayloadTooLarge(AppError):
    status, code, message = 413, "payload_too_large", "The request is too large."


class Unprocessable(AppError):
    status, code, message = 422, "unprocessable", "The values could not be accepted."


class TooManyRequests(AppError):
    status, code, message = 429, "rate_limited", "Too many requests. Try again shortly."


def envelope(err: AppError):
    body = {"error": {"code": err.code, "message": err.message,
                      "request_id": getattr(g, "request_id", None)}}
    if err.details:
        body["error"]["details"] = err.details
    resp = jsonify(body)
    resp.status_code = err.status
    if err.retry_after:
        resp.headers["Retry-After"] = str(err.retry_after)
    return resp


def init_errors(app: Flask) -> None:
    @app.before_request
    def _request_id():
        g.request_id = uuid.uuid4().hex[:16]

    @app.errorhandler(AppError)
    def _app_error(err: AppError):
        return envelope(err)

    @app.errorhandler(HTTPException)
    def _http_error(err: HTTPException):
        mapped = AppError(err.description or err.name)
        mapped.status = err.code or 500
        mapped.code = (err.name or "error").lower().replace(" ", "_")
        return envelope(mapped)

    @app.errorhandler(Exception)
    def _unhandled(err: Exception):
        log.exception("unhandled error request_id=%s", getattr(g, "request_id", "-"))
        return envelope(AppError())
