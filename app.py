"""Provenance Guard — Flask API.

M3 scope: POST /submit runs the first signal (LLM), assigns a content_id,
writes a structured audit-log entry, and returns JSON. confidence/attribution/
label are placeholders until M4 (scoring) and M5 (labels). GET /log surfaces
the audit log.
"""
import os
import uuid
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

import storage
from signals import llm_signal
from scoring import stylometric_signal, score, generate_label

load_dotenv()

app = Flask(__name__)
storage.init_db()

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=[],
    storage_uri="memory://",
)


@app.route("/submit", methods=["POST"])
@limiter.limit("10 per minute;100 per day")
def submit():
    body = request.get_json(silent=True) or {}
    text = body.get("text")
    creator_id = body.get("creator_id")
    if not text or not creator_id:
        return jsonify({"error": "text and creator_id are required"}), 400

    content_id = str(uuid.uuid4())
    llm_score = llm_signal(text)
    stylo_score = stylometric_signal(text)
    confidence, attribution = score(llm_score, stylo_score)
    label = generate_label(attribution, confidence)

    storage.log_submission(
        content_id, creator_id, text,
        llm_score=llm_score, stylo_score=stylo_score,
        confidence=confidence, attribution=attribution,
    )

    return jsonify({
        "content_id": content_id,
        "attribution": attribution,
        "confidence": confidence,
        "label": label,
        "llm_score": llm_score,
        "stylo_score": stylo_score,
    })


@app.route("/appeal", methods=["POST"])
def appeal():
    body = request.get_json(silent=True) or {}
    content_id = body.get("content_id")
    creator_reasoning = body.get("creator_reasoning")
    if not content_id or not creator_reasoning:
        return jsonify(
            {"error": "content_id and creator_reasoning are required"}
        ), 400

    result = storage.log_appeal(content_id, creator_reasoning)
    if result is None:
        return jsonify({"error": "content_id not found"}), 404

    return jsonify({
        "content_id": content_id,
        "status": "under_review",
        "message": ("Your appeal has been received. This content is now under "
                    "review by a human moderator."),
    })


@app.route("/log", methods=["GET"])
def log():
    return jsonify({"entries": storage.get_log()})


if __name__ == "__main__":
    app.run(debug=True, port=5000)