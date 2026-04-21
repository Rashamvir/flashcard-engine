document.addEventListener("DOMContentLoaded", () => {
    setupUploadForm();
    setupDeleteConfirmations();
    setupPracticeSession();
    setupProgressChart();
});

function setupUploadForm() {
    const form = document.querySelector("[data-upload-form]");
    if (!form) return;
    const fileInput = form.querySelector("#pdf_file");
    const fileUploadText = form.querySelector("[data-file-upload-text]");

    if (fileInput && fileUploadText) {
        fileInput.addEventListener("change", () => {
            const selectedFile = fileInput.files && fileInput.files[0];
            fileUploadText.textContent = selectedFile ? selectedFile.name : "Choose a PDF file";
        });
    }

    form.addEventListener("submit", () => {
        const button = form.querySelector("[data-upload-button]");
        const label = form.querySelector("[data-upload-label]");
        if (!button || !label) return;

        button.disabled = true;
        button.classList.add("loading");
        label.textContent = "Generating cards...";
    });
}

function setupDeleteConfirmations() {
    const forms = document.querySelectorAll("[data-delete-form]");
    forms.forEach((form) => {
        form.addEventListener("submit", (event) => {
            const confirmed = window.confirm("Delete this deck and all of its cards?");
            if (!confirmed) {
                event.preventDefault();
            }
        });
    });
}

function setupPracticeSession() {
    const app = document.querySelector("[data-practice-app]");
    if (!app) return;

    const flashcardScene = app.querySelector("[data-flashcard-scene]");
    const flipButton = app.querySelector("[data-flip-button]");
    const ratingRow = app.querySelector("[data-rating-row]");
    const prompt = app.querySelector("[data-flip-prompt]");
    const question = app.querySelector("[data-question]");
    const answer = app.querySelector("[data-answer]");
    const reviewedText = app.querySelector("[data-reviewed-count]");
    const totalText = app.querySelector("[data-total-count]");
    const progressFill = app.querySelector("[data-progress-fill]");
    const flashcardSection = app.querySelector("[data-flashcard-section]");
    const completionScreen = app.querySelector("[data-completion-screen]");
    const statGotIt = app.querySelector("[data-stat-got-it]");
    const statAlmost = app.querySelector("[data-stat-almost]");
    const statMissed = app.querySelector("[data-stat-missed]");
    const deckSelector = app.querySelector("[data-deck-selector]");
    const deckId = Number(app.dataset.deckId || 0);
    const practiceMode = app.dataset.practiceMode || "due";

    let currentCard = parseJson(app.dataset.initialCard);
    let totalCards = Number(app.dataset.totalCards || 0);
    let reviewedCount = 0;
    const reviewedIds = [];
    const sessionStats = { 5: 0, 3: 0, 0: 0 };

    if (deckSelector) {
        deckSelector.addEventListener("change", (event) => {
            const deckId = event.target.value;
            if (!deckId) return;
            window.location.href = `/practice/${deckId}`;
        });
    }

    if (!flashcardScene || !flipButton || !ratingRow || !question || !answer) {
        return;
    }

    const renderProgress = () => {
        reviewedText.textContent = String(reviewedCount);
        totalText.textContent = String(totalCards);
        const pct = totalCards > 0 ? (reviewedCount / totalCards) * 100 : 0;
        progressFill.style.width = `${pct}%`;
    };

    const setCardContent = (card) => {
        currentCard = card;
        if (!card) return;
        question.textContent = card.question;
        answer.textContent = card.answer;
        flashcardScene.classList.remove("flipped");
        ratingRow.classList.add("hidden");
        prompt.classList.remove("hidden");
    };

    const finishSession = () => {
        flashcardSection.classList.add("hidden");
        completionScreen.classList.remove("hidden");
        statGotIt.textContent = String(sessionStats[5]);
        statAlmost.textContent = String(sessionStats[3]);
        statMissed.textContent = String(sessionStats[0]);
    };

    const flipCard = () => {
        if (!currentCard) return;
        flashcardScene.classList.toggle("flipped");
        const isFlipped = flashcardScene.classList.contains("flipped");
        ratingRow.classList.toggle("hidden", !isFlipped);
        prompt.classList.toggle("hidden", isFlipped);
    };

    flashcardScene.addEventListener("click", flipCard);
    flipButton.addEventListener("click", flipCard);

    app.querySelectorAll("[data-quality]").forEach((button) => {
        button.addEventListener("click", async () => {
            if (!currentCard) return;

            const quality = Number(button.dataset.quality);
            try {
                const response = await fetch("/practice/review", {
                    method: "POST",
                    headers: {
                        "Content-Type": "application/json",
                    },
                    body: JSON.stringify({
                        card_id: currentCard.id,
                        deck_id: deckId,
                        mode: practiceMode,
                        reviewed_ids: reviewedIds,
                        quality,
                    }),
                });

                const payload = await response.json();
                if (!response.ok) {
                    throw new Error(payload.error || "Unable to submit review.");
                }

                reviewedCount += 1;
                reviewedIds.push(currentCard.id);
                sessionStats[quality] += 1;
                renderProgress();

                if (payload.completed || !payload.next_card) {
                    finishSession();
                } else {
                    setCardContent(payload.next_card);
                }
            } catch (error) {
                window.alert(error.message);
            }
        });
    });

    renderProgress();
    if (currentCard) {
        setCardContent(currentCard);
    } else if (totalCards === 0 && flashcardSection && completionScreen.classList.contains("hidden") === false) {
        finishSession();
    }
}

function setupProgressChart() {
    const canvas = document.getElementById("progressChart");
    if (!canvas || typeof Chart === "undefined") return;

    fetch("/api/progress-data")
        .then((response) => response.json())
        .then((data) => {
            new Chart(canvas, {
                type: "bar",
                data: {
                    labels: data.labels,
                    datasets: [
                        {
                            label: "Mastered",
                            data: data.mastered,
                            backgroundColor: "#4caf50",
                            borderRadius: 8,
                        },
                        {
                            label: "Stable",
                            data: data.stable,
                            backgroundColor: "#5c6bc0",
                            borderRadius: 8,
                        },
                        {
                            label: "Learning",
                            data: data.learning,
                            backgroundColor: "#f44336",
                            borderRadius: 8,
                        },
                    ],
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {
                        x: {
                            grid: {
                                display: false,
                            },
                            border: {
                                display: false,
                            },
                        },
                        y: {
                            beginAtZero: true,
                            ticks: {
                                precision: 0,
                            },
                            grid: {
                                display: false,
                            },
                            border: {
                                display: false,
                            },
                        },
                    },
                    plugins: {
                        legend: {
                            position: "top",
                            labels: {
                                usePointStyle: true,
                                boxWidth: 10,
                            },
                        },
                    },
                },
            });
        })
        .catch(() => {
            // Keep the page usable if chart data fails to load.
        });
}

function parseJson(value) {
    try {
        return JSON.parse(value);
    } catch {
        return null;
    }
}
