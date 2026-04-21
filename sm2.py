from datetime import date, timedelta


def update_sm2(card, quality):
    # quality: 0 = missed, 3 = almost, 5 = got it
    if quality >= 3:
        if card.repetitions == 0:
            card.interval_days = 1
        elif card.repetitions == 1:
            card.interval_days = 6
        else:
            card.interval_days = round(card.interval_days * card.ease_factor)
        card.repetitions += 1
        card.ease_factor = max(1.3, card.ease_factor + 0.1 - (5 - quality) * 0.08)
    else:
        card.repetitions = 0
        card.interval_days = 1
    card.next_review_date = date.today() + timedelta(days=card.interval_days)
    return card
