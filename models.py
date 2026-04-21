import hashlib
from datetime import date, datetime

from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import UniqueConstraint


db = SQLAlchemy()


def build_card_fingerprint(question: str, answer: str) -> str:
    normalized_question = " ".join(question.lower().split())
    normalized_answer = " ".join(answer.lower().split())
    payload = f"{normalized_question}::{normalized_answer}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class Deck(db.Model):
    __tablename__ = "decks"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    pdf_filename = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    cards = db.relationship(
        "Card",
        back_populates="deck",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Card.created_at.desc()",
    )
    study_sessions = db.relationship(
        "StudySession",
        back_populates="deck",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Card(db.Model):
    __tablename__ = "cards"
    __table_args__ = (UniqueConstraint("deck_id", "card_fingerprint", name="uq_card_per_deck"),)

    id = db.Column(db.Integer, primary_key=True)
    deck_id = db.Column(db.Integer, db.ForeignKey("decks.id", ondelete="CASCADE"), nullable=False)
    question = db.Column(db.Text, nullable=False)
    answer = db.Column(db.Text, nullable=False)
    card_fingerprint = db.Column(db.String(64), nullable=False)
    ease_factor = db.Column(db.Float, default=2.5, nullable=False)
    interval_days = db.Column(db.Integer, default=1, nullable=False)
    repetitions = db.Column(db.Integer, default=0, nullable=False)
    next_review_date = db.Column(db.Date, default=date.today, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    deck = db.relationship("Deck", back_populates="cards")
    study_sessions = db.relationship(
        "StudySession",
        back_populates="card",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class StudySession(db.Model):
    __tablename__ = "study_sessions"

    id = db.Column(db.Integer, primary_key=True)
    card_id = db.Column(db.Integer, db.ForeignKey("cards.id", ondelete="CASCADE"), nullable=False)
    deck_id = db.Column(db.Integer, db.ForeignKey("decks.id", ondelete="CASCADE"), nullable=False)
    quality = db.Column(db.Integer, nullable=False)
    reviewed_at = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)

    card = db.relationship("Card", back_populates="study_sessions")
    deck = db.relationship("Deck", back_populates="study_sessions")
