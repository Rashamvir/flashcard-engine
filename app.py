import os
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
from flask import Flask, flash, jsonify, redirect, render_template, request, url_for
from pypdf import PdfReader
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from werkzeug.utils import secure_filename

from groq_helper import FlashcardGenerationError, generate_flashcards
from models import Card, Deck, StudySession, build_card_fingerprint, db
from sm2 import update_sm2


load_dotenv()

basedir = os.path.abspath(os.path.dirname(__file__))
BASE_DIR = Path(__file__).resolve().parent
UPLOAD_FOLDER = os.path.join(basedir, "uploads")
UPLOAD_DIR = Path(UPLOAD_FOLDER)
UPLOAD_DIR.mkdir(exist_ok=True)


app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///" + os.path.join(basedir, "flashcards.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "flashcard-engine-dev-secret")
app.config["MAX_CONTENT_LENGTH"] = 20 * 1024 * 1024

db.init_app(app)


def ensure_database():
    db.session.remove()
    db.engine.dispose()
    inspector = inspect(db.engine)
    existing_tables = set(inspector.get_table_names())
    required_tables = {"decks", "cards", "study_sessions"}
    if not required_tables.issubset(existing_tables):
        db.create_all()


with app.app_context():
    ensure_database()


@app.before_request
def ensure_database_before_request():
    ensure_database()


def allowed_pdf(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() == "pdf"


def extract_pdf_text(file_path: Path) -> str:
    reader = PdfReader(str(file_path))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(pages).strip()


def serialize_card(card: Card) -> dict:
    return {
        "id": card.id,
        "question": card.question,
        "answer": card.answer,
    }


def build_unique_cards(deck_id: int, flashcards: list[dict]) -> list[Card]:
    unique_cards = []
    seen_fingerprints: set[str] = set()

    for card in flashcards:
        question = (card.get("question") or "").strip()
        answer = (card.get("answer") or "").strip()
        if not question or not answer:
            continue

        fingerprint = build_card_fingerprint(question, answer)
        if fingerprint in seen_fingerprints:
            continue

        seen_fingerprints.add(fingerprint)
        unique_cards.append(
            Card(
                deck_id=deck_id,
                question=question,
                answer=answer,
                card_fingerprint=fingerprint,
            )
        )

    return unique_cards


def get_due_cards(deck_id: int) -> list[Card]:
    return (
        Card.query.filter(
            Card.deck_id == deck_id,
            Card.next_review_date <= date.today(),
        )
        .order_by(Card.next_review_date.asc(), Card.created_at.asc(), Card.id.asc())
        .all()
    )


def get_practice_cards(deck_id: int, mode: str = "due", exclude_card_ids: set[int] | None = None) -> list[Card]:
    exclude_card_ids = exclude_card_ids or set()
    query = Card.query.filter(Card.deck_id == deck_id)

    if mode != "all":
        query = query.filter(Card.next_review_date <= date.today())

    cards = query.order_by(Card.created_at.asc(), Card.id.asc()).all()
    return [card for card in cards if card.id not in exclude_card_ids]


def classify_card_progress(card: Card) -> str:
    if not card.study_sessions:
        return "learning"

    latest_session = max(card.study_sessions, key=lambda session: session.reviewed_at)

    if latest_session.quality == 5:
        return "mastered"
    if latest_session.quality == 3:
        return "stable"
    return "learning"


def build_progress_snapshot() -> tuple[list[dict], int]:
    decks = Deck.query.order_by(Deck.created_at.desc()).all()
    today = date.today()
    streak = calculate_streak()
    stats = []

    for deck in decks:
        total_cards = len(deck.cards)
        mastered = 0
        stable = 0
        learning = 0

        for card in deck.cards:
            bucket = classify_card_progress(card)
            if bucket == "mastered":
                mastered += 1
            elif bucket == "stable":
                stable += 1
            else:
                learning += 1

        due_today = sum(1 for card in deck.cards if card.next_review_date <= today)
        mastery_pct = round((mastered / total_cards) * 100) if total_cards else 0

        stats.append(
            {
                "id": deck.id,
                "name": deck.name,
                "created_at": deck.created_at,
                "total_cards": total_cards,
                "mastered": mastered,
                "stable": stable,
                "learning": learning,
                "due_today": due_today,
                "mastery_pct": mastery_pct,
            }
        )

    return stats, streak


def calculate_streak() -> int:
    reviewed_dates = {
        reviewed_at.date()
        for (reviewed_at,) in db.session.query(StudySession.reviewed_at).distinct().all()
    }

    if not reviewed_dates:
        return 0

    streak = 0
    current_day = date.today()
    while current_day in reviewed_dates:
        streak += 1
        current_day -= timedelta(days=1)
    return streak


@app.route("/")
def index():
    return render_template("index.html")


@app.post("/upload")
def upload():
    deck_name = request.form.get("deck_name", "").strip()
    pdf_file = request.files.get("pdf_file")
    deck = None

    if not deck_name:
        flash("Please enter a deck name.", "error")
        return redirect(url_for("index"))

    if not pdf_file or not pdf_file.filename:
        flash("Please choose a PDF file to upload.", "error")
        return redirect(url_for("index"))

    if not allowed_pdf(pdf_file.filename):
        flash("Only PDF uploads are supported.", "error")
        return redirect(url_for("index"))

    safe_filename = f"{uuid4().hex}_{secure_filename(pdf_file.filename)}"
    temp_path = UPLOAD_DIR / safe_filename

    try:
        pdf_file.save(temp_path)
        extracted_text = extract_pdf_text(temp_path)

        if not extracted_text:
            flash("The uploaded PDF did not contain extractable text.", "error")
            return redirect(url_for("index"))

        flashcards = generate_flashcards(extracted_text, deck_name)
        if not flashcards:
            flash("No flashcards were generated from this PDF.", "error")
            return redirect(url_for("index"))

        deck = Deck(name=deck_name, pdf_filename=pdf_file.filename)
        db.session.add(deck)
        db.session.commit()

        cards = build_unique_cards(deck.id, flashcards)

        if not cards:
            db.session.delete(deck)
            db.session.commit()
            flash("Flashcard generation returned empty cards.", "error")
            return redirect(url_for("index"))

        db.session.add_all(cards)
        db.session.commit()
        flash(f"{len(cards)} cards generated for deck: {deck.name}", "success")
        return redirect(url_for("decks"))

    except FlashcardGenerationError as exc:
        db.session.rollback()
        if deck and deck.id:
            existing_deck = Deck.query.get(deck.id)
            if existing_deck:
                db.session.delete(existing_deck)
                db.session.commit()
        app.logger.exception("Flashcard generation failed.")
        flash(str(exc), "error")
        return redirect(url_for("index"))
    except IntegrityError:
        db.session.rollback()
        if deck and deck.id:
            existing_deck = Deck.query.get(deck.id)
            if existing_deck:
                db.session.delete(existing_deck)
                db.session.commit()
        app.logger.exception("Duplicate card insert prevented.")
        flash("Duplicate flashcards were detected and removed. Please try the upload again.", "error")
        return redirect(url_for("index"))
    except Exception:
        db.session.rollback()
        if deck and deck.id:
            existing_deck = Deck.query.get(deck.id)
            if existing_deck:
                db.session.delete(existing_deck)
                db.session.commit()
        app.logger.exception("Unexpected PDF processing failure.")
        flash("Something went wrong while processing the PDF.", "error")
        return redirect(url_for("index"))
    finally:
        if temp_path.exists():
            temp_path.unlink()


@app.get("/decks")
def decks():
    deck_stats, _ = build_progress_snapshot()
    return render_template("decks.html", deck_stats=deck_stats)


@app.post("/decks/<int:deck_id>/delete")
def delete_deck(deck_id: int):
    try:
        deck = Deck.query.get_or_404(deck_id)
        deck_name = deck.name
        db.session.delete(deck)
        db.session.commit()
        flash(f'Deck "{deck_name}" deleted.', "success")
    except Exception:
        db.session.rollback()
        flash("Unable to delete the selected deck.", "error")

    return redirect(url_for("decks"))


@app.get("/practice")
@app.get("/practice/<int:deck_id>")
def practice(deck_id: int | None = None):
    mode = request.args.get("mode", "due")
    if mode not in {"due", "all"}:
        mode = "due"

    all_decks = Deck.query.order_by(Deck.created_at.desc()).all()
    selected_deck = Deck.query.get_or_404(deck_id) if deck_id else None
    practice_cards = get_practice_cards(deck_id, mode=mode) if deck_id else []
    initial_card = serialize_card(practice_cards[0]) if practice_cards else None

    return render_template(
        "practice.html",
        all_decks=all_decks,
        selected_deck=selected_deck,
        practice_mode=mode,
        practice_count=len(practice_cards),
        initial_card=initial_card,
    )


@app.get("/decks/<int:deck_id>/cards")
def deck_cards(deck_id: int):
    deck = Deck.query.get_or_404(deck_id)
    cards = (
        Card.query.filter_by(deck_id=deck.id)
        .order_by(Card.created_at.asc(), Card.id.asc())
        .all()
    )
    return render_template("deck_cards.html", deck=deck, cards=cards)


@app.post("/practice/review")
def review_card():
    payload = request.get_json(silent=True) or {}
    card_id = payload.get("card_id")
    deck_id = payload.get("deck_id")
    mode = payload.get("mode", "due")
    reviewed_ids = payload.get("reviewed_ids", [])
    try:
        quality = int(payload.get("quality"))
    except (TypeError, ValueError):
        quality = None

    if card_id is None or deck_id is None or quality not in (0, 3, 5):
        return jsonify({"error": "Invalid review payload."}), 400

    if mode not in {"due", "all"}:
        mode = "due"

    try:
        reviewed_id_set = {int(item) for item in reviewed_ids}
    except (TypeError, ValueError):
        reviewed_id_set = set()

    try:
        card = Card.query.get_or_404(card_id)
        update_sm2(card, quality)

        session = StudySession(card_id=card.id, deck_id=card.deck_id, quality=quality)
        db.session.add(session)
        db.session.commit()

        reviewed_id_set.add(card.id)
        remaining_cards = get_practice_cards(int(deck_id), mode=mode, exclude_card_ids=reviewed_id_set)
        next_card = serialize_card(remaining_cards[0]) if remaining_cards else None

        return jsonify(
            {
                "completed": next_card is None,
                "next_card": next_card,
                "remaining_due": len(remaining_cards),
            }
        )
    except Exception:
        db.session.rollback()
        app.logger.exception("Review submission failed.")
        return jsonify({"error": "Unable to save your review right now."}), 500


@app.get("/progress")
def progress():
    deck_stats, streak = build_progress_snapshot()
    return render_template("progress.html", deck_stats=deck_stats, streak=streak)


@app.get("/api/progress-data")
def progress_data():
    deck_stats, streak = build_progress_snapshot()
    return jsonify(
        {
            "streak": streak,
            "labels": [deck["name"] for deck in deck_stats],
            "mastered": [deck["mastered"] for deck in deck_stats],
            "stable": [deck["stable"] for deck in deck_stats],
            "learning": [deck["learning"] for deck in deck_stats],
        }
    )


if __name__ == "__main__":
    app.run(debug=True)
