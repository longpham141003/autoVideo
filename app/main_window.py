from __future__ import annotations

import os
import json
import re
import shutil
import sys
from pathlib import Path

from PyQt6.QtCore import QThread, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QAction, QBrush, QColor, QDesktopServices, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .chrome_manager import ChromeSession
from .config import APP_DIR, load_settings, load_ui_state, rebase_app_path, save_settings, save_ui_state
from .pipeline_worker import PipelineWorker
from .project_store import ProjectStore
from .shorts_builder import build_project_shorts
from .text_to_voice_queue import DELIVERY_STYLES, LANGUAGES, VOICES, ensure_text_to_voice_server
from .veo3_runner import login_veo3_account, run_veo3_generate_only, run_veo3_generate_shorts_only, run_veo3_then_edit
from .video_editor import (
    MERGED_VIDEO_NAME,
    background_video_mode_options,
    collect_story_image_files,
    collect_video_files,
    collect_voice_files,
    merge_project_videos,
    render_project_video,
)


STATUS_COLORS = {
    "waiting": ("Đang đợi", "#f3f4f6", "#4b5563"),
    "queued": ("Trong hàng", "#e0f2fe", "#075985"),
    "running": ("Đang chạy", "#fef3c7", "#92400e"),
    "done": ("Xong", "#dcfce7", "#166534"),
    "error": ("Lỗi", "#fee2e2", "#991b1b"),
    "stopped": ("Đã dừng", "#e5e7eb", "#374151"),
    "": ("-", "#f9fafb", "#6b7280"),
}

# Temporarily disabled while Google Flow/VEO3 image UI is unstable.
# Keep the VEO3 image code paths in place so they can be re-enabled later.
VEO3_IMAGE_GENERATION_ENABLED = False

CONTENT_PRESETS = {
    "growth_30": {
        "label": "30 phut",
        "workflow": "growth_story_5_chapters.json",
        "chapter_count": 5,
        "target_word_count": 4300,
        "expected_duration": "30 minutes",
    },
    "long_form": {
        "label": "1 tieng",
        "workflow": "long_form_8_chapters.json",
        "chapter_count": 8,
        "target_word_count": 6800,
        "expected_duration": "45-60 minutes",
    },
    "auto_story": {
        "label": "Auto Story",
        "workflow": "auto_story_daily_podcast.json",
        "chapter_count": 6,
        "target_word_count": 6500,
        "expected_duration": "45 minutes",
    },
}

AUTO_STORY_GENRES = [
    ("family_revenge", "Family Drama / Revenge - phan boi, cong ly cam xuc"),
    ("mystery", "Mystery - bi mat, twist, cliffhanger"),
    ("true_crime", "True Crime - dieu tra, vu an, bi an"),
    ("audio_drama", "Audio Drama - nhieu nhan vat, nhieu canh"),
    ("romance", "Romance - tinh cam, chua lanh, happy ending"),
    ("sci_fi_fantasy", "Sci-Fi / Fantasy - the gioi, nhiem vu, bi mat"),
    ("historical_drama", "Historical Drama - lich su, chien tranh, di cu, ky uc"),
    ("memoir_life_story", "Memoir / Life Story - chuyen doi, bai hoc 35+"),
    ("self_help_story", "Self-help Story - cau chuyen + bai hoc"),
]

PRIORITY_AUTO_STORY_GENRES = {"family_revenge", "mystery", "true_crime"}

AUTO_STORY_LENGTHS = [
    ("30", "30 phut / 5 chapter", 5, 4300, "30 minutes"),
    ("45", "45 phut / 6 chapter", 6, 6500, "45 minutes"),
    ("60", "60 phut / 8 chapter", 8, 8500, "60 minutes"),
]

AUTO_STORY_TONES = [
    ("serious cinematic", "Nghiem tuc, dien anh"),
    ("warm emotional", "Am ap, cam xuc"),
    ("tense suspense", "Cang thang, hoi hop"),
    ("hopeful reflective", "Hy vong, suy ngam"),
    ("dramatic but grounded", "Kich tinh nhung doi thuong"),
]

AUTO_STORY_NARRATORS = [
    ("solo narrator", "Mot giong ke chuyen"),
    ("multi-character audio drama", "Nhieu nhan vat nhu phim audio"),
    ("first-person memoir voice", "Ngoi thu nhat, nhu hoi ky"),
]

AUTO_STORY_HOOKS = [
    ("cold open mystery", "Mo dau bang bi an ngay lap tuc"),
    ("shocking confession", "Mo dau bang loi thu nhan soc"),
    ("found evidence", "Mo dau bang bang chung vua tim thay"),
    ("emotional betrayal", "Mo dau bang phan boi cam xuc"),
    ("quiet question", "Mo dau bang cau hoi am anh"),
]

AUTO_STORY_CLIFFHANGERS = [
    ("medium", "Vua phai"),
    ("high", "Manh"),
    ("low", "Nhe"),
]

AUTO_STORY_GENRE_PRESETS = {
    "true_crime": {
        "tone": "serious cinematic",
        "narrator": "solo narrator",
        "hook": "found evidence",
        "cliffhanger": "high",
    },
    "mystery": {
        "tone": "serious cinematic",
        "narrator": "solo narrator",
        "hook": "cold open mystery",
        "cliffhanger": "high",
    },
    "romance": {
        "tone": "warm emotional",
        "narrator": "first-person memoir voice",
        "hook": "quiet question",
        "cliffhanger": "medium",
    },
    "family_revenge": {
        "tone": "dramatic but grounded",
        "narrator": "solo narrator",
        "hook": "emotional betrayal",
        "cliffhanger": "medium",
    },
    "historical_drama": {
        "tone": "hopeful reflective",
        "narrator": "first-person memoir voice",
        "hook": "found evidence",
        "cliffhanger": "medium",
    },
    "memoir_life_story": {
        "tone": "hopeful reflective",
        "narrator": "first-person memoir voice",
        "hook": "quiet question",
        "cliffhanger": "low",
    },
    "self_help_story": {
        "tone": "hopeful reflective",
        "narrator": "solo narrator",
        "hook": "emotional betrayal",
        "cliffhanger": "low",
    },
    "sci_fi_fantasy": {
        "tone": "tense suspense",
        "narrator": "solo narrator",
        "hook": "cold open mystery",
        "cliffhanger": "high",
    },
    "audio_drama": {
        "tone": "tense suspense",
        "narrator": "multi-character audio drama",
        "hook": "shocking confession",
        "cliffhanger": "high",
    },
}

FAMILY_DRAMA_SETTING_ROTATION = [
    ("elder_care_receipts", "Elder care at home. A daughter who does all daily care finds paid caregiver receipts for days nobody came. Core betrayal: sibling billing the parent while doing nothing. Proof object: caregiver invoices, doorbell logs, pharmacy refill dates."),
    ("family_house_quitclaim", "Inherited family house. The protagonist finds a quitclaim deed filed while the parent was hospitalized. Core betrayal: sibling quietly moved the house into their name. Proof object: county deed record, hospital admission timestamp, notary signature."),
    ("wedding_table_humiliation", "Wedding day betrayal. At the reception, the family seats the protagonist away from the parents and announces a changed inheritance or family decision. Core betrayal: public humiliation masked as celebration. Proof object: seating chart, printed toast, venue email."),
    ("divorce_stepchild_loyalty", "Divorce and stepchild loyalty. A stepchild asks for help after discovering the ex-spouse hid money meant for care or school. Core betrayal: adults using a child as leverage. Proof object: school bill, custody email, bank transfer."),
    ("nursing_home_power_of_attorney", "Nursing home conflict. The protagonist discovers a new power of attorney signed after the parent failed a cognitive test. Core betrayal: sibling isolating the parent to control money. Proof object: POA form, visitor log, doctor's note."),
    ("hospital_discharge_signature", "Hospital decision. A sibling signs a discharge or treatment refusal against the parent's wishes to protect money or convenience. Core betrayal: health sacrificed for control. Proof object: discharge papers, nurse voicemail, insurance call log."),
    ("missing_bank_account", "Will and bank account. The protagonist finds a closed joint account that should have paid for the parent's care. Core betrayal: family member draining savings while preaching responsibility. Proof object: bank statement, ATM footage note, old will."),
    ("farm_land_option", "Family farm. A developer option contract appears on land the parent promised would stay in the family. Core betrayal: one child selling heritage land under financial pressure. Proof object: option contract, soil survey, unpaid equipment loan."),
    ("restaurant_recipe_sale", "Family restaurant. A sibling sells the family recipe/name to a chain while telling everyone the restaurant is failing. Core betrayal: turning legacy into a private payout. Proof object: licensing draft, vendor invoices, hidden reservation book."),
    ("payroll_small_company", "Small family company. The protagonist finds ghost payroll under relatives' names while real workers went unpaid. Core betrayal: family using loyalty to steal from the business. Proof object: payroll report, timecards, tax notice."),
    ("funeral_flower_invoice", "Funeral reveal. A strange invoice in the funeral packet exposes who paid for secrecy before the parent died. Core betrayal: grief used to hide a final lie. Proof object: funeral bill, old photograph, attorney envelope."),
    ("birthday_video_message", "Birthday gathering. A family birthday video contains an accidental background confession. Core betrayal: the smiling family has already made a decision behind the protagonist's back. Proof object: phone video, group chat, gift receipt."),
    ("neighbor_witness_mailbox", "Small community neighbors. A neighbor returns misdelivered mail that exposes family paperwork. Core betrayal: everyone nearby suspected the truth except the protagonist. Proof object: certified letter, mailbox camera, neighbor statement."),
    ("forced_signature_tablet", "Forced signature. A parent was pressured to sign documents on a tablet they did not understand. Core betrayal: digital convenience used to steal consent. Proof object: e-sign audit trail, tablet location data, witness text."),
    ("caregiver_sibling_schedule", "Sibling caregiver fight. The sibling claiming credit for care was absent on the hardest days. Core betrayal: stealing moral credit and inheritance leverage. Proof object: care calendar, medication logs, gas receipts."),
    ("retirement_account_transfer", "Retirement savings. A retirement account transfer is disguised as emergency family expenses. Core betrayal: safety money redirected to cover another relative's debt. Proof object: transfer form, creditor letter, tax penalty notice."),
    ("vacation_home_lock_change", "Family vacation home. Locks are changed before a holiday weekend because one sibling has secretly listed the house. Core betrayal: shared memories turned into a private sale. Proof object: real estate listing, locksmith receipt, old family key."),
    ("graduation_tuition_check", "Graduation and tuition. A tuition check never reached the school, and the family blamed the student. Core betrayal: a young person's future used to hide adult debt. Proof object: returned check, bursar email, bank memo."),
    ("holiday_dinner_envelope", "Holiday dinner. During Thanksgiving or Christmas, an envelope at the table exposes a hidden legal change. Core betrayal: family unity used as stage management. Proof object: sealed envelope, attorney letter, seating plan."),
    ("life_insurance_beneficiary", "Life insurance beneficiary. The beneficiary changed days before a medical decline. Core betrayal: someone converted care access into financial control. Proof object: beneficiary form, hospice visit log, call recording."),
]

AUTO_STORY_SETTING_ROTATIONS = {
    "family_revenge": FAMILY_DRAMA_SETTING_ROTATION,
    "true_crime": [
        ("cold_case_storage", "Fictional true-crime spine: an old storage unit contains case boxes nobody claimed. Core mystery: a small-town death was filed wrong. Proof object: case label, dated photo, missing evidence receipt."),
        ("wrongful_confession", "Fictional true-crime spine: a confession tape sounds rehearsed years after conviction. Core mystery: who coached the confession. Proof object: audio tape, interview transcript, attorney note."),
        ("missing_nurse_shift", "Fictional true-crime spine: a nurse vanished after swapping shifts. Core mystery: hospital schedule hides the last person who saw her. Proof object: shift roster, parking stub, pager log."),
        ("church_basement_records", "Fictional true-crime spine: church records reveal donations tied to a disappearance. Core mystery: charity money covered a private crime. Proof object: ledger, basement key, old receipt."),
        ("lake_house_911_gap", "Fictional true-crime spine: a 911 call has a missing minute. Core mystery: what was cut before police arrived. Proof object: dispatch tape, phone bill, lake-house photo."),
        ("factory_accident_cover", "Fictional true-crime spine: a factory death was ruled accidental too quickly. Core mystery: workplace pressure and a witness who left town. Proof object: safety memo, timecard, broken helmet."),
        ("motel_guestbook", "Fictional true-crime spine: a motel guestbook contradicts a suspect timeline. Core mystery: someone checked in under a family name. Proof object: guestbook, room key, rain-soaked receipt."),
        ("county_fair_abduction", "Fictional true-crime spine: a child disappeared at a county fair decades ago. Core mystery: a family friend was never interviewed. Proof object: fair ticket, photo booth strip, police map."),
        ("arson_insurance_pattern", "Fictional true-crime spine: a pattern of fires follows insurance distress. Core mystery: desperation, coaching, and a quiet fixer. Proof object: claim file, cassette, electrical invoice."),
        ("retired_detective_box", "Fictional true-crime spine: a retired detective leaves one unsolved box to his daughter. Core mystery: why he protected a suspect. Proof object: redacted report, apology letter, fingerprint card."),
        ("cemetery_name_change", "Fictional true-crime spine: cemetery records show one grave was renamed. Core mystery: identity swap after a death. Proof object: burial ledger, old obituary, undertaker invoice."),
        ("boat_ramp_witness", "Fictional true-crime spine: a boat-ramp witness recants after twenty years. Core mystery: who paid for silence. Proof object: tackle-shop receipt, witness note, lake patrol log."),
        ("domestic_call_pattern", "Fictional true-crime spine: repeated domestic calls were closed without reports. Core mystery: a protected family name. Proof object: dispatch log, bodycam gap, neighbor voicemail."),
        ("school_bus_route", "Fictional true-crime spine: an old school bus route reveals an impossible alibi. Core mystery: the route changed only one day. Proof object: route sheet, bus radio log, yearbook photo."),
        ("diner_last_meal", "Fictional true-crime spine: a diner receipt proves the victim met someone after their supposed death time. Core mystery: staged timeline. Proof object: receipt, waitress memory, security still."),
        ("pawnshop_ring", "Fictional true-crime spine: a pawned ring connects two unrelated cases. Core mystery: why police missed the link. Proof object: pawn ticket, ring inscription, old case card."),
        ("probate_secret", "Fictional true-crime spine: probate papers expose motive in a suspicious death. Core mystery: inheritance disguised as accident. Proof object: will draft, medicine bottle, calendar."),
        ("storm_drain_evidence", "Fictional true-crime spine: storm repairs uncover an evidence bag. Core mystery: why it was never logged. Proof object: evidence tag, drainage map, old badge number."),
        ("anonymous_tipbox", "Fictional true-crime spine: an anonymous tip box contains twenty-year-old letters. Core mystery: a witness tried to speak but was ignored. Proof object: letters, postmarks, precinct stamp."),
        ("reopened_inquest", "Fictional true-crime spine: a coroner's retired assistant finds an inquest error. Core mystery: cause of death was softened for politics. Proof object: autopsy draft, coroner note, campaign flyer."),
    ],
    "mystery": [
        ("locked_attic_letter", "Mystery spine: a locked attic contains a letter addressed to someone who never lived there. Core question: who erased a family member. Proof object: attic key, letter, old portrait."),
        ("bell_without_tower", "Mystery spine: a bell rings from a building that lost its bell years ago. Core question: who wants an old lie reopened. Proof object: bell fragment, message, town record."),
        ("library_return_card", "Mystery spine: a library return card appears inside a book checked out by a dead person. Core question: who used their name. Proof object: card, marginal note, checkout ledger."),
        ("neighbor_window_light", "Mystery spine: a neighbor's window light turns on every night in an empty house. Core question: what is being hidden there. Proof object: power bill, footprint, old key."),
        ("missing_recipe_box", "Mystery spine: a recipe box contains coded notes instead of recipes. Core question: what did the grandmother track. Proof object: index cards, ingredient substitutions, map."),
        ("train_station_locker", "Mystery spine: an abandoned train-station locker opens with a key from a funeral coat. Core question: why the dead kept paying rent. Proof object: locker ticket, photo, cash envelope."),
        ("wrong_family_photo", "Mystery spine: a framed family photo shows a stranger standing where the father should be. Core question: who was edited out of history. Proof object: negative, photo receipt, diary."),
        ("basement_radio", "Mystery spine: a basement radio receives a voice naming events before they happen. Core question: broadcast, recording, or hoax. Proof object: radio log, cassette, storm schedule."),
        ("hotel_room_312", "Mystery spine: a hotel room was booked every year by someone using a family name. Core question: secret meeting or hidden identity. Proof object: registry, key card, anniversary date."),
        ("cemetery_flowers", "Mystery spine: fresh flowers appear on an unmarked grave every month. Core question: who remembers the person buried there. Proof object: florist bill, cemetery map, nameplate."),
        ("auction_box", "Mystery spine: an auction box contains documents from three unrelated families. Core question: why one person collected them. Proof object: auction tag, folded deed, receipt."),
        ("weather_diary", "Mystery spine: an old diary records weather that never happened. Core question: the diary is code for something else. Proof object: diary, newspaper archive, marked dates."),
        ("empty_safe", "Mystery spine: an empty safe has fresh scratches and one envelope left behind. Core question: what mattered enough to steal. Proof object: safe dust mark, envelope, inventory list."),
        ("retirement_home_piano", "Mystery spine: a retirement-home piano hides a roll of names. Core question: residents shared a secret from decades ago. Proof object: piano roll, staff memo, old program."),
        ("missing_birth_record", "Mystery spine: a birth record was replaced with a blank duplicate. Core question: who changed a child's origin. Proof object: registry copy, hospital bracelet, clerk note."),
        ("lake_map_pin", "Mystery spine: a lake map has pins marking places where nothing officially happened. Core question: hidden pattern across years. Proof object: map, pins, old local paper."),
        ("postcard_after_death", "Mystery spine: postcards arrive from a person long believed dead. Core question: survival, impersonation, or confession. Proof object: postmark, handwriting, travel receipt."),
        ("choir_room_tape", "Mystery spine: a choir-room tape records a conversation after the church was locked. Core question: who had a second key. Proof object: tape, key log, hymn sheet."),
        ("probate_clock", "Mystery spine: a family clock stops at the same time as an old disappearance. Core question: symbolic clue or mechanical evidence. Proof object: clock repair tag, police note, photo."),
        ("snow_footprints", "Mystery spine: fresh footprints cross a yard to a door that has not opened in years. Core question: who entered without breaking the lock. Proof object: footprints, lock record, hidden room."),
    ],
    "romance": [
        ("second_chance_letters", "Romance spine: old unsent letters reveal two people loved each other before life separated them. Core tension: regret versus present courage. Proof object: letters, returned envelope, photo."),
        ("library_book_notes", "Romance spine: two lonely adults exchange notes in a library book without knowing each other. Core tension: anonymity versus vulnerability. Proof object: margin notes, due-date card, bookmark."),
        ("widower_garden", "Romance spine: a widower and a neighbor rebuild a neglected garden together. Core tension: grief, guilt, and permission to love again. Proof object: seed packet, old garden plan, wedding ring."),
        ("small_town_reunion", "Romance spine: high-school sweethearts meet at a reunion after decades. Core tension: old misunderstanding versus adult truth. Proof object: yearbook note, dance photo, apology letter."),
        ("bakery_competition", "Romance spine: two rival bakery owners compete for a town contract. Core tension: pride, family pressure, and attraction. Proof object: recipe card, bid form, burnt batch."),
        ("caregiver_son", "Romance spine: an adult child caring for a parent meets the parent’s physical therapist. Core tension: exhaustion versus being seen. Proof object: appointment card, missed call, care journal."),
        ("bookshop_inheritance", "Romance spine: two strangers inherit halves of a failing bookshop. Core tension: one wants to sell, one wants to save. Proof object: will, inventory list, hidden inscription."),
        ("road_trip_apology", "Romance spine: two former partners must drive cross-country to settle a family matter. Core tension: old hurt and forced honesty. Proof object: map, voicemail, motel receipt."),
        ("radio_dedication", "Romance spine: a radio dedication from decades ago resurfaces. Core tension: public memory and private regret. Proof object: station log, cassette, caller note."),
        ("hospital_volunteer", "Romance spine: two hospital volunteers bond over caring for strangers. Core tension: fear of loss and choosing tenderness. Proof object: volunteer badge, patient card, cafeteria receipt."),
        ("divorce_class", "Romance spine: two recently divorced people meet in a practical finance class. Core tension: rebuilding identity and trust. Proof object: workbook, coffee receipt, budget note."),
        ("lake_cabin_repairs", "Romance spine: a woman repairing a family cabin hires the man who once broke her heart. Core tension: memory, pride, and changed lives. Proof object: repair invoice, old key, carved initials."),
        ("community_theater", "Romance spine: two adults cast opposite each other in a community play. Core tension: staged romance exposing real feelings. Proof object: script, costume pin, closing-night note."),
        ("lost_wedding_ring", "Romance spine: a lost wedding ring returns to a widow through an unexpected man. Core tension: loyalty to the past versus future. Proof object: ring, pawn slip, beach photo."),
        ("farmers_market", "Romance spine: market vendors clash over space and quietly help each other survive. Core tension: competition and hidden kindness. Proof object: stall permit, rain tarp, ledger."),
        ("old_house_restoration", "Romance spine: an old-house restoration pairs two people with opposite ideas of home. Core tension: control, grief, and belonging. Proof object: blueprint, wallpaper sample, found note."),
        ("teacher_parent", "Romance spine: a teacher and a single parent connect over a struggling student. Core tension: boundaries, shame, and trust. Proof object: report card, art project, meeting note."),
        ("choir_partners", "Romance spine: two older adults are paired for a church/community choir duet. Core tension: vulnerability in public. Proof object: sheet music, rehearsal schedule, old recording."),
        ("animal_rescue", "Romance spine: two people co-foster a rescued animal after a storm. Core tension: temporary care becoming permanent feeling. Proof object: vet bill, shelter form, collar tag."),
        ("train_delay", "Romance spine: a long train delay strands two strangers with linked pasts. Core tension: coincidence, confession, and timing. Proof object: ticket, missed voicemail, old newspaper clipping."),
    ],
    "historical_drama": [
        ("wwii_homefront_letter", "Historical drama spine: a WWII homefront letter contradicts a family legend. Core tension: duty, silence, and inherited shame. Proof object: wartime letter, ration book, service photo."),
        ("dust_bowl_farm", "Historical drama spine: a Dust Bowl family must choose who leaves and who stays. Core tension: survival versus loyalty. Proof object: deed, seed receipt, train ticket."),
        ("mill_strike", "Historical drama spine: a steel-mill strike splits a family. Core tension: worker dignity versus hunger. Proof object: strike flyer, pay envelope, injury report."),
        ("immigrant_trunk", "Historical drama spine: an immigrant trunk reveals a changed surname and lost sibling. Core tension: reinvention and erased roots. Proof object: trunk tag, ship manifest, prayer card."),
        ("civil_rights_bus", "Historical drama spine: a bus-station incident changes a family’s future. Core tension: courage, fear, and public witness. Proof object: ticket stub, newspaper clipping, arrest receipt."),
        ("flu_epidemic_1918", "Historical drama spine: a 1918 flu diary exposes a sacrifice hidden for generations. Core tension: care, quarantine, and grief. Proof object: diary, death notice, medicine bottle."),
        ("railroad_town", "Historical drama spine: a railroad town loses its station and a family loses its status. Core tension: progress versus memory. Proof object: timetable, layoff notice, family photo."),
        ("coal_mine_cavein", "Historical drama spine: a coal mine cave-in report hides who was warned. Core tension: labor, corruption, and guilt. Proof object: mine map, lamp tag, company memo."),
        ("war_bride_secret", "Historical drama spine: a war bride’s papers reveal a marriage nobody accepted. Core tension: prejudice, love, and family denial. Proof object: marriage certificate, photo, telegram."),
        ("orphan_train", "Historical drama spine: an orphan-train record reveals a child was renamed. Core tension: belonging and identity. Proof object: placement card, Bible inscription, train list."),
        ("great_migration_house", "Historical drama spine: a family house bought during the Great Migration becomes contested. Core tension: hard-won ownership versus later betrayal. Proof object: deed, rent receipt, church bulletin."),
        ("prohibition_speakeasy", "Historical drama spine: a Prohibition-era ledger connects a family business to a secret crime. Core tension: survival, morality, and reputation. Proof object: ledger, hidden bottle, police note."),
        ("depression_bank_closure", "Historical drama spine: a bank closure destroys one family and enriches another. Core tension: trust, money, and old resentment. Proof object: passbook, foreclosure notice, bank receipt."),
        ("vietnam_telegram", "Historical drama spine: a Vietnam-era telegram arrives at the wrong house. Core tension: grief assigned to the wrong family. Proof object: telegram, draft card, photo."),
        ("native_land_record", "Historical drama spine: an old county record reveals land taken through pressure and silence. Core tension: inheritance and historical harm. Proof object: survey map, court record, family letter."),
        ("women_factory_workers", "Historical drama spine: women factory workers keep a wartime secret after men return. Core tension: independence and erasure. Proof object: factory badge, payroll list, group photo."),
        ("hurricane_evacuation", "Historical drama spine: a historic hurricane evacuation separated relatives for decades. Core tension: disaster choices and family blame. Proof object: shelter list, postcard, water-damaged photo."),
        ("one_room_schoolhouse", "Historical drama spine: a one-room schoolhouse closing reveals a teacher’s hidden sacrifice. Core tension: education, class, and debt. Proof object: attendance book, scholarship letter, school bell."),
        ("county_poor_farm", "Historical drama spine: poor-farm records reveal an ancestor was abandoned. Core tension: shame and compassion across generations. Proof object: intake ledger, quilt, death record."),
        ("old_courthouse_fire", "Historical drama spine: a courthouse fire destroyed records but one clerk saved copies. Core tension: truth surviving official loss. Proof object: copybook, seal stamp, burned file."),
    ],
    "memoir_life_story": [
        ("mother_recipe_memory", "Memoir spine: a recipe triggers memories of a difficult mother. Core lesson: love can be imperfect and still formative. Proof object: recipe card, apron, grocery receipt."),
        ("first_job_factory", "Memoir spine: a first factory job teaches dignity and boundaries. Core lesson: work shapes identity but should not consume it. Proof object: timecard, lunch pail, first paycheck."),
        ("caregiver_burnout", "Memoir spine: years of caregiving force the narrator to admit resentment. Core lesson: duty without help becomes grief. Proof object: pill organizer, calendar, hospital bracelet."),
        ("divorce_apartment", "Memoir spine: moving into a small apartment after divorce becomes a rebirth. Core lesson: loss can return a person to themselves. Proof object: lease, folding chair, key."),
        ("father_tools", "Memoir spine: inherited tools reveal a father's quiet language of love. Core lesson: some parents apologize through action. Proof object: toolbox, handwritten label, worn hammer."),
        ("empty_nest_room", "Memoir spine: cleaning a child's old room exposes what the parent missed. Core lesson: love changes after letting go. Proof object: school photo, note, packed box."),
        ("late_education", "Memoir spine: returning to school in middle age changes family dynamics. Core lesson: growth can embarrass people who need you small. Proof object: class schedule, grade report, backpack."),
        ("sobriety_anniversary", "Memoir spine: a sobriety anniversary forces a reckoning with family harm. Core lesson: repair is slower than apology. Proof object: chip, letter, unopened voicemail."),
        ("immigrant_parent", "Memoir spine: translating for a parent as a child shaped adulthood. Core lesson: responsibility came too early. Proof object: appointment card, immigration form, school note."),
        ("lost_friendship", "Memoir spine: an old friendship ended over silence, not betrayal. Core lesson: neglect can wound like cruelty. Proof object: birthday card, old email, photo."),
        ("small_town_return", "Memoir spine: returning to a hometown after decades reveals both change and old patterns. Core lesson: place remembers versions of us. Proof object: yearbook, house key, local paper."),
        ("medical_scare", "Memoir spine: a medical scare changes what the narrator values. Core lesson: ordinary days become urgent. Proof object: test result, waiting-room bracelet, voicemail."),
        ("grandparent_clock", "Memoir spine: a grandparent's clock connects generations. Core lesson: inheritance can be time, not money. Proof object: clock, repair tag, family story."),
        ("failed_business", "Memoir spine: closing a small business teaches humility. Core lesson: failure can still carry honor. Proof object: closing sign, ledger, last receipt."),
        ("estranged_sibling", "Memoir spine: a sibling reunion after years of estrangement is awkward and necessary. Core lesson: forgiveness may be partial. Proof object: text message, hospital form, childhood photo."),
        ("military_homecoming", "Memoir spine: a homecoming is not as simple as people expect. Core lesson: survival and belonging are different. Proof object: duffel bag, medal box, bus ticket."),
        ("widow_first_winter", "Memoir spine: the first winter after losing a spouse becomes a map of grief. Core lesson: routines can save a life. Proof object: snow shovel, coat, grocery list."),
        ("adoption_search", "Memoir spine: searching for birth family complicates gratitude and identity. Core lesson: one truth does not erase another. Proof object: adoption file, DNA match, letter."),
        ("retirement_identity", "Memoir spine: retirement creates emptiness after a lifetime of usefulness. Core lesson: identity must be rebuilt. Proof object: retirement watch, calendar, old badge."),
        ("apology_never_received", "Memoir spine: the narrator stops waiting for an apology. Core lesson: peace can come without confession. Proof object: unsent letter, old phone number, keepsake."),
    ],
    "self_help_story": [
        ("boundaries_caregiver", "Self-help story spine: a caregiver learns boundaries after burnout. Core lesson: saying no can protect love. Proof object: calendar, missed appointment, honest conversation."),
        ("money_after_divorce", "Self-help story spine: rebuilding finances after divorce. Core lesson: clarity beats shame. Proof object: budget notebook, credit statement, first paid-off bill."),
        ("late_career_reset", "Self-help story spine: a middle-aged worker changes careers after layoff. Core lesson: identity is larger than job title. Proof object: resume, rejection email, training certificate."),
        ("decluttering_grief", "Self-help story spine: decluttering a parent's house becomes emotional healing. Core lesson: memory is not the same as objects. Proof object: donation box, photo album, kept item."),
        ("health_wakeup", "Self-help story spine: a health scare forces daily habit change. Core lesson: small routines restore control. Proof object: lab result, walking shoes, meal plan."),
        ("toxic_family_call", "Self-help story spine: one phone call exposes a toxic family pattern. Core lesson: not every role assigned by family must be accepted. Proof object: voicemail, journal, boundary script."),
        ("loneliness_community", "Self-help story spine: loneliness after kids leave leads to community. Core lesson: connection requires repeated small risks. Proof object: class flyer, coffee invite, attendance card."),
        ("forgiveness_limits", "Self-help story spine: forgiveness without reconciliation. Core lesson: peace is not access. Proof object: letter, blocked number, therapy note."),
        ("retirement_routine", "Self-help story spine: retirement depression improves through routine. Core lesson: purpose can be designed. Proof object: weekly schedule, volunteer badge, garden gloves."),
        ("confidence_after_betrayal", "Self-help story spine: betrayal damages confidence, then rebuilds through competence. Core lesson: action restores self-trust. Proof object: checklist, repaired sink, signed contract."),
        ("careful_friendship", "Self-help story spine: making friends later in life after mistrust. Core lesson: consistency matters more than intensity. Proof object: recurring lunch, text thread, shared errand."),
        ("parenting_adult_children", "Self-help story spine: learning not to rescue adult children financially. Core lesson: help can become harm. Proof object: bank transfer, unpaid bill, boundary plan."),
        ("sleep_and_anxiety", "Self-help story spine: anxiety improves when sleep and routines are protected. Core lesson: the body is part of the mind. Proof object: sleep log, phone alarm, doctor note."),
        ("downsizing_home", "Self-help story spine: downsizing after decades in one house. Core lesson: a smaller life can be freer. Proof object: floor plan, moving boxes, sold sign."),
        ("shame_to_skill", "Self-help story spine: shame about technology turns into learning. Core lesson: embarrassment is not a stop sign. Proof object: class receipt, password notebook, first video call."),
        ("grief_group", "Self-help story spine: a grief group helps someone speak honestly. Core lesson: pain shrinks when witnessed. Proof object: name tag, tissue packet, shared story."),
        ("anger_management", "Self-help story spine: anger after betrayal becomes structured action. Core lesson: anger can be information, not a driver. Proof object: unsent email, walking route, legal checklist."),
        ("second_act_creativity", "Self-help story spine: rediscovering art/music/writing after decades. Core lesson: creativity does not expire. Proof object: sketchbook, old instrument, community notice."),
        ("medical_advocacy", "Self-help story spine: advocating for a parent in the medical system. Core lesson: calm documentation creates power. Proof object: symptom log, nurse names, discharge sheet."),
        ("starting_over_small", "Self-help story spine: starting over after a public failure. Core lesson: small promises kept privately rebuild a life. Proof object: morning list, first client, thank-you note."),
    ],
    "sci_fi_fantasy": [
        ("memory_archive", "Sci-fi/fantasy spine: a public memory archive contains a memory the protagonist never lived. Core mystery: stolen life or future warning. Proof object: memory shard, access log, family mark."),
        ("generation_ship_will", "Sci-fi spine: a generation ship inheritance file names someone who should not exist. Core tension: family legacy and system lies. Proof object: ship registry, cryo tag, sealed will."),
        ("last_oracle_receipt", "Fantasy spine: an oracle leaves a receipt instead of a prophecy. Core mystery: fate hidden in ordinary accounting. Proof object: receipt, market token, burned thread."),
        ("city_under_sleep", "Fantasy spine: a city sleeps one hour too long every winter. Core mystery: who steals the hour. Proof object: clock key, dream journal, snow map."),
        ("terraform_farm", "Sci-fi spine: a frontier farm on a terraformed moon starts rejecting one family. Core tension: land, inheritance, and AI judgment. Proof object: soil report, colony deed, drone footage."),
        ("dragon_debt", "Fantasy spine: a family debt to a dragon comes due after three generations. Core tension: duty versus self-determination. Proof object: scale, contract, old lullaby."),
        ("robot_caregiver", "Sci-fi spine: an elder-care robot refuses a family command. Core mystery: it knows the parent was being manipulated. Proof object: care log, override code, recorded phrase."),
        ("portal_house", "Fantasy spine: an inherited house opens into a room that changes owners' memories. Core tension: family secrets and magical property. Proof object: door key, room ledger, portrait."),
        ("time_loop_dinner", "Sci-fi spine: a holiday dinner repeats until one family truth is spoken. Core tension: denial and accountability. Proof object: repeated toast, broken plate, clock."),
        ("spell_inheritance", "Fantasy spine: a will transfers not money but a dangerous spell. Core tension: power, resentment, and responsibility. Proof object: will, ink seal, family grimoire."),
        ("colony_vote", "Sci-fi spine: a colony vote will decide who gets oxygen priority. Core tension: family politics under survival pressure. Proof object: vote ledger, oxygen meter, council recording."),
        ("ghost_satellite", "Sci-fi spine: a dead relative's voice returns through an old satellite. Core mystery: recording, AI, or something stranger. Proof object: signal log, family tape, orbital map."),
        ("witch_trial_descendant", "Fantasy spine: descendants of an old trial inherit a curse when a document is found. Core tension: public shame and private truth. Proof object: trial transcript, charm, bloodline chart."),
        ("android_heir", "Sci-fi spine: an android is named heir to a family estate. Core tension: personhood, grief, and money. Proof object: legal file, maintenance memory, final message."),
        ("sea_wall_city", "Sci-fi spine: a flooded future city hides family records behind a sea wall. Core tension: class, survival, and erased ancestry. Proof object: waterproof ledger, tide map, access pass."),
        ("enchanted_retirement_home", "Fantasy spine: residents at a retirement home trade memories as currency. Core tension: dignity and exploitation. Proof object: memory coin, visitor log, missing birthday."),
        ("parallel_sibling", "Sci-fi spine: a parallel-world sibling arrives claiming the protagonist made the wrong choice. Core tension: regret and identity. Proof object: duplicate photo, impossible ID, shared scar."),
        ("kingdom_caregiver", "Fantasy spine: a royal caregiver discovers the monarch's children are stealing magic from them. Core tension: elder care, power, and inheritance. Proof object: spell chart, medicine cup, royal seal."),
        ("ai_family_court", "Sci-fi spine: an AI family court assigns caregiving duties by algorithm. Core tension: fairness versus love. Proof object: court output, appeal file, home sensor data."),
        ("lost_constellation", "Fantasy spine: a family constellation disappears from the sky when a promise is broken. Core tension: myth, legacy, and repair. Proof object: star map, grandmother's story, cracked pendant."),
    ],
    "audio_drama": [
        ("diner_blackout", "Audio drama spine: a diner blackout traps townspeople while one confession changes everything. Core engine: ensemble secrets under pressure. Proof object: emergency radio, register tape, whispered call."),
        ("hospital_waiting_room", "Audio drama spine: strangers in a hospital waiting room discover their emergencies are connected. Core engine: overlapping family crises. Proof object: pager, intake form, voicemail."),
        ("funeral_home_mixup", "Audio drama spine: a funeral-home paperwork mixup exposes two families' secrets. Core engine: dark humor and grief. Proof object: wrong envelope, memorial program, phone message."),
        ("storm_shelter", "Audio drama spine: a storm shelter fills with neighbors who know too much. Core engine: rising tension during one night. Proof object: radio warning, wet notebook, generator log."),
        ("community_theater_fire", "Audio drama spine: a community theater evacuation reveals a staged accident. Core engine: actors, old grudges, real danger. Proof object: prop list, call sheet, scorch mark."),
        ("radio_call_in", "Audio drama spine: a late-night radio call-in show receives a confession from someone nearby. Core engine: voice-only suspense. Proof object: call log, tape delay, caller phrase."),
        ("retirement_home_game", "Audio drama spine: a retirement-home card game turns into a tribunal. Core engine: older characters with long memories. Proof object: playing card, visitor book, old IOU."),
        ("town_council_meeting", "Audio drama spine: a town council meeting about budget cuts exposes family corruption. Core engine: public argument, private stakes. Proof object: agenda, microphone recording, invoice."),
        ("elevator_stuck", "Audio drama spine: people trapped in an elevator reveal linked lies. Core engine: contained real-time dialogue. Proof object: security intercom, dropped folder, phone battery."),
        ("wedding_rehearsal", "Audio drama spine: a wedding rehearsal collapses when an old promise resurfaces. Core engine: ensemble family conflict. Proof object: vow draft, seating chart, voice memo."),
        ("school_board_night", "Audio drama spine: a school board meeting about one student reveals adult betrayal. Core engine: public morality and private hypocrisy. Proof object: report card, email printout, recording."),
        ("bus_terminal_delay", "Audio drama spine: a delayed bus strands relatives and strangers with one shared destination. Core engine: confessions in transit. Proof object: ticket, suitcase tag, missed call."),
        ("probate_office", "Audio drama spine: multiple relatives wait in a probate office as a will is read. Core engine: inheritance and performance. Proof object: will packet, old key, lawyer note."),
        ("small_claims_court", "Audio drama spine: a small-claims case expands into a family reckoning. Core engine: legal setting, emotional truth. Proof object: receipt, photo, witness list."),
        ("church_potluck", "Audio drama spine: a church potluck turns tense after a donor list leaks. Core engine: politeness cracking. Proof object: donation sheet, casserole dish, anonymous note."),
        ("hotel_conference", "Audio drama spine: a family business conference hides a succession coup. Core engine: backroom deals and public speeches. Proof object: name badge, agenda, contract draft."),
        ("911_dispatch_shift", "Audio drama spine: dispatchers on one night realize calls are connected to a family secret. Core engine: audio logs and mounting dread. Proof object: call timestamps, map pins, headset recording."),
        ("jury_room", "Audio drama spine: jurors deliberating a case realize one juror has a personal connection. Core engine: moral pressure and reveal. Proof object: exhibit photo, juror note, timeline."),
        ("train_car", "Audio drama spine: one train car becomes a pressure cooker after a passenger disappears. Core engine: sound-rich ensemble mystery. Proof object: ticket punch, luggage tag, conductor log."),
        ("family_group_call", "Audio drama spine: a family group call about a parent turns into a live confrontation. Core engine: voices, interruptions, hidden recordings. Proof object: call recording, shared document, muted confession."),
    ],
}


def content_preset(mode: str) -> dict:
    return CONTENT_PRESETS.get(str(mode or ""), CONTENT_PRESETS["growth_30"])


def open_path(path: str | Path) -> None:
    target = Path(path)
    if not target.exists():
        return
    if os.name == "nt":
        os.startfile(str(target))  # type: ignore[attr-defined]
        return
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))


class ChromeOpenThread(QThread):
    log = pyqtSignal(str)
    done = pyqtSignal(bool, str)

    def __init__(self, settings: dict, parent=None):
        super().__init__(parent)
        self.settings = settings

    def run(self) -> None:
        try:
            session = ChromeSession(self.settings)
            cdp_url = session.ensure_started(log=lambda msg: self.log.emit(str(msg)))
            self.done.emit(True, f"ChatGPT da san sang: {cdp_url}")
        except Exception as exc:
            self.done.emit(False, str(exc))


class TextToVoiceOpenThread(QThread):
    log = pyqtSignal(str)
    done = pyqtSignal(bool, str)

    def __init__(self, settings: dict, parent=None):
        super().__init__(parent)
        self.settings = settings

    def run(self) -> None:
        try:
            url = ensure_text_to_voice_server(self.settings, log=lambda msg: self.log.emit(str(msg)))
            self.done.emit(True, url)
        except Exception as exc:
            self.done.emit(False, str(exc))


class VideoEditThread(QThread):
    log = pyqtSignal(str)
    done = pyqtSignal(bool, str)

    def __init__(self, settings: dict, project_dir: str | Path, parent=None, background_files: list[Path] | None = None):
        super().__init__(parent)
        self.settings = settings
        self.project_dir = Path(project_dir)
        self.background_files = background_files

    def run(self) -> None:
        try:
            result = render_project_video(
                self.project_dir,
                self.settings,
                log=lambda msg: self.log.emit(str(msg)),
                background_files=self.background_files,
            )
            self.done.emit(True, str(result.output_path))
        except Exception as exc:
            self.done.emit(False, str(exc))


class Veo3LoginThread(QThread):
    log = pyqtSignal(str)
    done = pyqtSignal(bool, str)

    def __init__(self, settings: dict, parent=None):
        super().__init__(parent)
        self.settings = settings

    def run(self) -> None:
        try:
            result = login_veo3_account(self.settings, log=lambda msg: self.log.emit(str(msg)))
            ok = bool(result.get("success"))
            self.done.emit(ok, str(result.get("message") or result))
        except Exception as exc:
            self.done.emit(False, str(exc))


class VideoMergeThread(QThread):
    log = pyqtSignal(str)
    done = pyqtSignal(bool, str)

    def __init__(self, settings: dict, project_dir: str | Path, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.project_dir = Path(project_dir)

    def run(self) -> None:
        try:
            output_path = merge_project_videos(self.project_dir, self.settings, log=lambda msg: self.log.emit(str(msg)))
            self.done.emit(True, str(output_path))
        except Exception as exc:
            self.done.emit(False, str(exc))


class Veo3GenerateThread(QThread):
    log = pyqtSignal(str)
    done = pyqtSignal(bool, str)

    def __init__(self, settings: dict, project_dir: str | Path, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.project_dir = Path(project_dir)

    def run(self) -> None:
        try:
            images = run_veo3_generate_only(self.project_dir, self.settings, log=lambda msg: self.log.emit(str(msg)))
            self.done.emit(True, f"{len(images)} anh | {self.project_dir / 'veo_videos' / 'image'}")
        except Exception as exc:
            self.done.emit(False, str(exc))


class AutoStoryFullVideoThread(QThread):
    log = pyqtSignal(str)
    done = pyqtSignal(bool, str)

    def __init__(self, settings: dict, project_dir: str | Path, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.project_dir = Path(project_dir)

    def run(self) -> None:
        try:
            # VEO3 image generation is temporarily disabled. Keep the old
            # run_veo3_then_edit path available for later, but render directly now.
            result = render_project_video(
                self.project_dir,
                self.settings,
                log=lambda msg: self.log.emit(str(msg)),
                background_files=None,
            )
            self.done.emit(True, str(result.output_path))
        except Exception as exc:
            self.done.emit(False, str(exc))


class ShortsVeoGenerateThread(QThread):
    log = pyqtSignal(str)
    done = pyqtSignal(bool, str)

    def __init__(self, settings: dict, project_dir: str | Path, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.project_dir = Path(project_dir)

    def run(self) -> None:
        try:
            images = run_veo3_generate_shorts_only(self.project_dir, self.settings, log=lambda msg: self.log.emit(str(msg)))
            self.done.emit(True, f"{len(images)} anh Short | {self.project_dir / 'shorts'}")
        except Exception as exc:
            self.done.emit(False, str(exc))


class ShortsRenderThread(QThread):
    log = pyqtSignal(str)
    done = pyqtSignal(bool, str)

    def __init__(self, settings: dict, project_dir: str | Path, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.project_dir = Path(project_dir)

    def run(self) -> None:
        try:
            outputs = build_project_shorts(self.project_dir, self.settings, log=lambda msg: self.log.emit(str(msg)))
            self.done.emit(True, "\n".join(str(path) for path in outputs))
        except Exception as exc:
            self.done.emit(False, str(exc))


class TextViewer(QMainWindow):
    def __init__(self, title: str, text_path: str, parent=None):
        super().__init__(parent)
        self.text_path = Path(text_path)
        self.setWindowTitle(title)
        self.resize(920, 680)
        self.editor = QTextEdit()
        self.editor.setReadOnly(True)
        self.editor.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        try:
            self.editor.setPlainText(self.text_path.read_text(encoding="utf-8"))
        except Exception as exc:
            self.editor.setPlainText(f"Không đọc được file:\n{self.text_path}\n\n{exc}")
        self.setCentralWidget(self.editor)

        toolbar = self.addToolBar("Thao tác")
        toolbar.setMovable(False)
        copy_action = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton), "Copy", self)
        copy_action.triggered.connect(self.copy_text)
        open_action = QAction(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon), "Mở thư mục", self)
        open_action.triggered.connect(lambda: open_path(self.text_path.parent))
        toolbar.addAction(copy_action)
        toolbar.addAction(open_action)

    def copy_text(self) -> None:
        QApplication.clipboard().setText(self.editor.toPlainText())


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = load_settings()
        self.ui_state = load_ui_state()
        self.project_store = ProjectStore(self.settings["projects_dir"])
        self.pipeline: PipelineWorker | None = None
        self.chrome_thread: ChromeOpenThread | None = None
        self.text_to_voice_thread: TextToVoiceOpenThread | None = None
        self.video_edit_thread: VideoEditThread | None = None
        self.veo3_login_thread: Veo3LoginThread | None = None
        self.veo3_generate_thread: Veo3GenerateThread | None = None
        self.auto_story_full_thread: AutoStoryFullVideoThread | None = None
        self.shorts_veo_thread: ShortsVeoGenerateThread | None = None
        self.shorts_render_thread: ShortsRenderThread | None = None
        self.video_merge_thread: VideoMergeThread | None = None
        self.current_project: Path | None = None
        self.chapter_rows: dict[int, int] = {}
        self.viewer_windows: list[TextViewer] = []
        self._restoring_ui_state = False
        self.auto_story_render_after_pipeline = False

        self.setWindowTitle("Tool Kịch Bản & Text to Voice")
        self.setMinimumSize(1020, 650)
        self.resize(1280, 760)
        self._build_ui()
        self._apply_style()
        self.reset_timeline()
        self.restore_ui_state()
        selected_project = str(self.ui_state.get("selected_project") or "").strip()
        self.reload_projects(select_path=Path(rebase_app_path(selected_project, require_exists=True)) if selected_project else None)

    def _build_ui(self) -> None:
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(14, 10, 14, 12)
        root_layout.setSpacing(8)

        header = QHBoxLayout()
        title_box = QVBoxLayout()
        title = QLabel("Tool Kịch Bản & Text to Voice")
        title.setObjectName("AppTitle")
        subtitle = QLabel("Tạo kịch bản bằng ChatGPT web; chương nào xong thì tự tạo audio bằng Text to Voice local.")
        subtitle.setObjectName("Subtitle")
        subtitle.setWordWrap(True)
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        header.addLayout(title_box, 1)
        self.chrome_button = QPushButton("Khởi động / đăng nhập ChatGPT")
        self.chrome_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon))
        self.chrome_button.clicked.connect(self.open_chrome)
        self.text_to_voice_button = QPushButton("Mở Text to Voice UI")
        self.text_to_voice_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DesktopIcon))
        self.text_to_voice_button.clicked.connect(self.open_text_to_voice)
        self.veo3_login_button = QPushButton("Login VEO3")
        self.veo3_login_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon))
        self.veo3_login_button.clicked.connect(self.login_veo3)
        self.run_button = QPushButton("Chạy tất cả")
        self.run_button.setObjectName("PrimaryButton")
        self.run_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.run_button.clicked.connect(self.start_pipeline)
        self.stop_button = QPushButton("Dừng")
        self.stop_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaStop))
        self.stop_button.clicked.connect(self.stop_pipeline)
        self.stop_button.setEnabled(False)
        header.addWidget(self.chrome_button)
        header.addWidget(self.text_to_voice_button)
        header.addWidget(self.veo3_login_button)
        header.addWidget(self.run_button)
        header.addWidget(self.stop_button)
        root_layout.addLayout(header)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_right_tabs())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([380, 900])
        root_layout.addWidget(splitter, 1)
        self.setCentralWidget(root)

    def _build_left_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("SidePanel")
        panel.setMinimumWidth(340)
        panel.setMaximumWidth(520)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget()
        content.setObjectName("SidePanelContent")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(16, 16, 16, 16)
        content_layout.setSpacing(12)
        scroll.setWidget(content)
        layout.addWidget(scroll)

        def prep_input(widget: QWidget, height: int = 38) -> QWidget:
            widget.setMinimumHeight(height)
            widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            return widget

        def add_field(parent_layout: QVBoxLayout, label: str, widget: QWidget) -> None:
            text = QLabel(label)
            text.setObjectName("FieldLabel")
            parent_layout.addWidget(text)
            parent_layout.addWidget(widget)

        story_group = QGroupBox("Đầu vào kịch bản")
        story_form = QVBoxLayout(story_group)
        story_form.setContentsMargins(12, 18, 12, 12)
        story_form.setSpacing(8)
        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("Tên project / tên câu chuyện")
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("Link YouTube nếu có")
        self.audience_input = QLineEdit("US audience, long-form YouTube listeners")
        self.rewrite_ratio_input = QLineEdit("35%")
        self.channel_input = QLineEdit("Wise Woman Revenge")
        self.language_input = QComboBox()
        self.language_input.setEditable(True)
        self.language_input.addItems(["English", "Vietnamese", "English with emotional narration"])
        self.content_mode_combo = QComboBox()
        for key, preset in CONTENT_PRESETS.items():
            if key == "auto_story":
                continue
            self.content_mode_combo.addItem(str(preset["label"]), key)
        self.content_mode_combo.currentIndexChanged.connect(lambda _=0: self.reset_timeline())
        self.background_mode_combo = QComboBox()
        self.populate_background_mode_combo(str(self.settings.get("background_video_mode") or "cooking"))
        add_field(story_form, "Tên project", prep_input(self.title_input))
        add_field(story_form, "Link YouTube", prep_input(self.url_input))
        add_field(story_form, "Kiểu video", prep_input(self.content_mode_combo))
        add_field(story_form, "Tệp khán giả", prep_input(self.audience_input))
        add_field(story_form, "Mức viết lại", prep_input(self.rewrite_ratio_input))
        add_field(story_form, "Tên kênh", prep_input(self.channel_input))
        add_field(story_form, "Ngôn ngữ", prep_input(self.language_input))
        add_field(story_form, "Kieu edit nen", prep_input(self.background_mode_combo))
        content_layout.addWidget(story_group)

        source_group = QGroupBox("Transcript / nội dung gốc")
        source_layout = QVBoxLayout(source_group)
        source_layout.setContentsMargins(12, 18, 12, 12)
        self.source_input = QTextEdit()
        self.source_input.setPlaceholderText("Dán transcript hoặc nội dung gốc vào đây.")
        self.source_input.setMinimumHeight(150)
        source_layout.addWidget(self.source_input)
        content_layout.addWidget(source_group)

        thumb_group = QGroupBox("Thumbnail gốc")
        thumb_layout = QVBoxLayout(thumb_group)
        thumb_layout.setContentsMargins(12, 18, 12, 12)
        thumb_row = QHBoxLayout()
        self.thumbnail_path_input = QLineEdit()
        self.thumbnail_path_input.setPlaceholderText("Ảnh thumbnail nếu cần trích text")
        prep_input(self.thumbnail_path_input)
        thumb_pick = QPushButton("Chọn ảnh")
        thumb_pick.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView))
        thumb_pick.clicked.connect(self.pick_thumbnail_file)
        thumb_row.addWidget(self.thumbnail_path_input, 1)
        thumb_row.addWidget(thumb_pick)
        self.thumb_text_input = QTextEdit()
        self.thumb_text_input.setPlaceholderText("Nếu đã có text thumbnail thì dán vào đây để bỏ qua bước đọc ảnh.")
        self.thumb_text_input.setMinimumHeight(68)
        thumb_layout.addLayout(thumb_row)
        thumb_layout.addWidget(self.thumb_text_input)
        content_layout.addWidget(thumb_group)

        character_group = QGroupBox("Ảnh nhân vật final")
        character_layout = QVBoxLayout(character_group)
        character_layout.setContentsMargins(12, 18, 12, 12)
        character_row = QHBoxLayout()
        self.character_path_input = QLineEdit()
        self.character_path_input.setPlaceholderText("Ảnh nhân vật 9:16 JPG/PNG/WebP để đặt bên phải video")
        prep_input(self.character_path_input)
        character_pick = QPushButton("Chọn ảnh")
        character_pick.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView))
        character_pick.clicked.connect(self.pick_character_file)
        character_row.addWidget(self.character_path_input, 1)
        character_row.addWidget(character_pick)
        character_layout.addLayout(character_row)
        content_layout.addWidget(character_group)

        voice_group = QGroupBox("Text to Voice")
        voice_layout = QVBoxLayout(voice_group)
        voice_layout.setContentsMargins(12, 18, 12, 12)
        voice_layout.setSpacing(8)
        self.ttv_language_combo = QComboBox()
        for code, name in LANGUAGES.items():
            self.ttv_language_combo.addItem(f"{name} ({code})", code)
        self.ttv_language_combo.currentIndexChanged.connect(self.reload_text_to_voice_voice_choices)
        self.ttv_voice_combo = QComboBox()
        self.ttv_voice_combo.setEditable(True)
        self.ttv_voice_combo.setMinimumContentsLength(24)
        self.ttv_delivery_combo = QComboBox()
        for key, label in DELIVERY_STYLES.items():
            self.ttv_delivery_combo.addItem(label, key)
        self.ttv_speed_input = QLineEdit(str(self.settings.get("text_to_voice_speed") or "1.0"))
        self.ttv_speed_input.setPlaceholderText("0.5 - 2.0")
        add_field(voice_layout, "Ngôn ngữ", prep_input(self.ttv_language_combo))
        add_field(voice_layout, "Giọng", prep_input(self.ttv_voice_combo))
        add_field(voice_layout, "Kiểu đọc", prep_input(self.ttv_delivery_combo))
        add_field(voice_layout, "Tốc độ", prep_input(self.ttv_speed_input))
        content_layout.addWidget(voice_group)
        self.reload_text_to_voice_controls()

        content_layout.addStretch(1)
        return panel

    def _build_right_tabs(self) -> QTabWidget:
        tabs = QTabWidget()
        tabs.setUsesScrollButtons(True)
        tabs.setElideMode(Qt.TextElideMode.ElideRight)
        tabs.addTab(self._build_run_tab(), "Đang chạy")
        tabs.addTab(self._build_results_tab(), "Kết quả")
        tabs.addTab(self._build_settings_tab(), "Cấu hình")
        tabs.insertTab(1, self._build_auto_story_tab(), "Tu tao truyen")
        return tabs

    def _build_auto_story_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        form_box = QGroupBox("Tu tao truyen moi")
        form = QGridLayout(form_box)
        form.setContentsMargins(12, 18, 12, 12)
        form.setHorizontalSpacing(10)
        form.setVerticalSpacing(8)

        self.auto_title_input = QLineEdit()
        self.auto_title_input.setPlaceholderText("De trong thi ChatGPT tu dat ten truyen")
        self.auto_genre_combo = QComboBox()
        self.populate_auto_story_genres()
        self.auto_genre_combo.currentIndexChanged.connect(self.apply_auto_story_genre_preset)
        self.auto_age_combo = QComboBox()
        self.auto_age_combo.addItems(["35-44", "45-54", "55+"])
        self.auto_length_combo = QComboBox()
        for key, label, _chapters, _words, _duration in AUTO_STORY_LENGTHS:
            self.auto_length_combo.addItem(label, key)
        self.auto_tone_combo = QComboBox()
        for value, label in AUTO_STORY_TONES:
            self.auto_tone_combo.addItem(label, value)
        self.auto_narrator_combo = QComboBox()
        for value, label in AUTO_STORY_NARRATORS:
            self.auto_narrator_combo.addItem(label, value)
        self.auto_hook_combo = QComboBox()
        for value, label in AUTO_STORY_HOOKS:
            self.auto_hook_combo.addItem(label, value)
        self.auto_cliffhanger_combo = QComboBox()
        for value, label in AUTO_STORY_CLIFFHANGERS:
            self.auto_cliffhanger_combo.addItem(label, value)
        self.auto_seed_input = QTextEdit()
        self.auto_seed_input.setPlaceholderText("Tuy chon. De trong thi tool tu tao y tuong moi dua tren the loai da chon.")
        self.auto_seed_input.setMinimumHeight(90)
        self.apply_auto_story_genre_preset()

        rows = [
            ("Ten truyen", self.auto_title_input),
            ("The loai", self.auto_genre_combo),
            ("Nhom tuoi", self.auto_age_combo),
            ("Do dai", self.auto_length_combo),
            ("Tone mac dinh", self.auto_tone_combo),
            ("Kieu ke mac dinh", self.auto_narrator_combo),
            ("Hook mac dinh", self.auto_hook_combo),
            ("Cliffhanger mac dinh", self.auto_cliffhanger_combo),
        ]
        for row, (label, widget) in enumerate(rows):
            form.addWidget(QLabel(label), row, 0)
            form.addWidget(widget, row, 1)
        form.addWidget(QLabel("Y tuong rieng"), len(rows), 0)
        form.addWidget(self.auto_seed_input, len(rows), 1)
        form.setColumnStretch(1, 1)
        layout.addWidget(form_box)

        note = QLabel("Chi can chon the loai, nhom tuoi va do dai. Tone, kieu ke, hook va cliffhanger da duoc gan combo mac dinh theo the loai.")
        note.setWordWrap(True)
        note.setObjectName("Subtitle")
        layout.addWidget(note)

        self.auto_full_video_check = QCheckBox("Tao luon video hoan chinh sau khi xong voice")
        self.auto_full_video_check.setChecked(True)
        layout.addWidget(self.auto_full_video_check)

        actions = QHBoxLayout()
        self.auto_story_run_button = QPushButton("Chay Auto Story")
        self.auto_story_run_button.setObjectName("PrimaryButton")
        self.auto_story_run_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.auto_story_run_button.clicked.connect(self.start_auto_story_pipeline)
        actions.addWidget(self.auto_story_run_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        layout.addStretch(1)
        return page

    def _build_run_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        self.project_label = QLabel("Project hiện tại: chưa chạy")
        self.project_label.setObjectName("ProjectLabel")
        layout.addWidget(self.project_label)

        self.timeline_table = QTableWidget(0, 4)
        self.timeline_table.setHorizontalHeaderLabels(["Mục", "Text", "Voice", "File"])
        self.timeline_table.verticalHeader().setVisible(False)
        self.timeline_table.verticalHeader().setDefaultSectionSize(34)
        self.timeline_table.setAlternatingRowColors(True)
        self.timeline_table.setWordWrap(False)
        self.timeline_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.timeline_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.timeline_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        self.timeline_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.timeline_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.timeline_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.timeline_table, 2)

        self.log_box = QTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setObjectName("LogBox")
        self.log_box.setMinimumHeight(130)
        self.log_box.setMaximumHeight(190)
        layout.addWidget(self.log_box)
        return page

    def _build_results_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        controls = QFrame()
        controls.setObjectName("ResultsControls")
        top = QGridLayout(controls)
        top.setContentsMargins(12, 10, 12, 10)
        top.setHorizontalSpacing(10)
        top.setVerticalSpacing(8)
        self.project_combo = QComboBox()
        self.project_combo.setMinimumWidth(260)
        self.project_combo.currentIndexChanged.connect(self.load_selected_project_results)
        refresh_btn = QPushButton("Tải lại")
        refresh_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload))
        refresh_btn.clicked.connect(lambda _checked=False: self.reload_projects())
        open_project_btn = QPushButton("Mở thư mục project")
        open_project_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        open_project_btn.clicked.connect(self.open_selected_project_folder)
        open_project_btn.setText("Mở project")
        open_veo_btn = QPushButton("Mở thư mục ảnh VEO")
        open_veo_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        open_veo_btn.clicked.connect(self.open_selected_veo_folder)
        open_veo_btn.setText("Mở ảnh")
        open_shorts_btn = QPushButton("Mở thư mục Shorts")
        open_shorts_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
        open_shorts_btn.clicked.connect(self.open_selected_shorts_folder)
        open_shorts_btn.setText("Mở Shorts")
        load_project_btn = QPushButton("Nạp project vào form")
        load_project_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton))
        load_project_btn.clicked.connect(self.load_selected_project_into_form)
        load_project_btn.setText("Nạp form")
        self.veo3_auto_button = QPushButton("Video dài: Tạo ảnh")
        self.veo3_auto_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.veo3_auto_button.clicked.connect(self.start_veo3_generate)
        self.veo3_auto_button.setText("Dài: Tạo ảnh")
        self.veo3_auto_button.setEnabled(VEO3_IMAGE_GENERATION_ENABLED)
        self.veo3_auto_button.setToolTip("Tạm tắt tạo ảnh VEO3 để dùng sau")
        self.merge_video_button = QPushButton("Ghép video")
        self.merge_video_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton))
        self.merge_video_button.clicked.connect(self.start_merge_video)
        self.merge_video_button.setText("Ghép video")
        self.merge_video_button.hide()
        self.auto_edit_button = QPushButton("Tự edit video + sub")
        self.auto_edit_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.auto_edit_button.clicked.connect(self.start_final_render)
        self.auto_edit_button.setText("Voice + edit")
        self.shorts_veo_button = QPushButton("Shorts: Tao anh")
        self.shorts_veo_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaPlay))
        self.shorts_veo_button.clicked.connect(self.start_shorts_veo_generate)
        self.shorts_veo_button.setText("Shorts: Tao anh")
        self.shorts_veo_button.setEnabled(VEO3_IMAGE_GENERATION_ENABLED)
        self.shorts_veo_button.setToolTip("Tạm tắt tạo ảnh VEO3 Shorts để dùng sau")
        self.shorts_render_button = QPushButton("Shorts: Voice + edit")
        self.shorts_render_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogApplyButton))
        self.shorts_render_button.clicked.connect(self.start_shorts_render)
        self.shorts_render_button.setText("Shorts: Voice + edit")
        top.addWidget(QLabel("Project"), 0, 0)
        top.addWidget(self.project_combo, 0, 1)
        top.addWidget(refresh_btn, 0, 2)
        top.setColumnStretch(1, 1)

        def add_button_row(row_index: int, label_text: str, buttons: tuple[QPushButton, ...]) -> None:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(8)
            label = QLabel(label_text)
            label.setObjectName("ActionSectionLabel")
            label.setMinimumWidth(82)
            row.addWidget(label)
            for button in buttons:
                button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
                row.addWidget(button)
            row.addStretch(1)
            top.addLayout(row, row_index, 0, 1, 3)

        add_button_row(1, "Project", (load_project_btn, open_project_btn))
        add_button_row(2, "Video dài", (open_veo_btn, self.veo3_auto_button, self.merge_video_button, self.auto_edit_button))
        add_button_row(3, "Shorts", (open_shorts_btn, self.shorts_veo_button, self.shorts_render_button))
        layout.addWidget(controls)

        self.results_table = QTableWidget(0, 5)
        self.results_table.setHorizontalHeaderLabels(["Loại", "Tên", "Text", "Audio", "Hành động"])
        self.results_table.verticalHeader().setVisible(False)
        self.results_table.verticalHeader().setDefaultSectionSize(38)
        self.results_table.setAlternatingRowColors(True)
        self.results_table.setWordWrap(False)
        self.results_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.results_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.results_table.horizontalHeader().setMinimumSectionSize(70)
        self.results_table.horizontalHeader().setStretchLastSection(False)
        self.results_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.results_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.results_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.results_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.results_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.results_table.setColumnWidth(0, 88)
        self.results_table.setColumnWidth(2, 76)
        self.results_table.setColumnWidth(3, 82)
        self.results_table.setColumnWidth(4, 176)
        layout.addWidget(self.results_table, 1)
        return page

    def _build_settings_tab(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(0, 0, 0, 0)
        scroll_layout.setSpacing(10)
        form_box = QGroupBox("Đường dẫn và profile")
        form = QFormLayout(form_box)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(8)
        self.setting_fields: dict[str, QLineEdit] = {}
        for key, label in [
            ("projects_dir", "Projects"),
            ("workflow_path", "Workflow"),
            ("chrome_profile_root", "Chrome profile root"),
            ("chrome_profile_name", "Chrome profile name"),
            ("chrome_cdp_port", "CDP port"),
            ("text_to_voice_root", "Text to Voice root"),
            ("text_to_voice_python", "Text to Voice Python"),
            ("text_to_voice_host", "Text to Voice host"),
            ("text_to_voice_port", "Text to Voice port"),
            ("text_to_voice_language", "Ngôn ngữ mặc định"),
            ("text_to_voice_voice", "Giọng mặc định"),
            ("text_to_voice_delivery", "Kiểu đọc mặc định"),
            ("text_to_voice_speed", "Tốc độ mặc định"),
            ("text_to_voice_max_chars", "Text to Voice max chars"),
            ("text_to_voice_timeout", "Text to Voice timeout"),
            ("text_to_voice_parallel_jobs", "Voice song song"),
            ("chrome_exe_path", "Chrome.exe"),
            ("veo3_root", "VEO3 root"),
            ("veo3_email", "VEO3 email"),
            ("veo3_password", "VEO3 password"),
            ("veo3_profile_name", "VEO3 profile"),
            ("veo3_aspect_ratio", "VEO3 aspect"),
            ("veo3_model", "VEO3 model"),
            ("create_image_model", "Model tao anh"),
            ("veo3_output_count", "VEO3 output/prompt"),
            ("veo3_multi_video", "VEO3 song song"),
            ("veo3_wait_gen_video", "VEO3 delay prompt"),
            ("veo3_retry_with_error", "VEO3 retry loi"),
            ("veo3_wait_resend_video", "VEO3 cho retry"),
            ("veo3_token_retry", "VEO3 retry token"),
            ("veo3_token_retry_delay", "VEO3 cho token"),
            ("veo3_download_images", "Tai anh kem VEO"),
            ("veo_character_consistency", "Dong nhat nhan vat"),
            ("final_video_width", "Video final width"),
            ("final_video_height", "Video final height"),
            ("final_video_layout", "Video final layout"),
            ("final_video_allow_loop", "Cho phep lap visual nen"),
            ("final_image_duration", "Moi anh keo dai giay"),
            ("final_video_character_path", "Ảnh nhân vật mặc định"),
            ("veo_prompt_limit", "So prompt anh"),
        ]:
            field = QLineEdit(str(self.settings.get(key) or ""))
            field.setMinimumHeight(34)
            field.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.setting_fields[key] = field
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(6)
            row.addWidget(field, 1)
            if key in {
                "projects_dir",
                "workflow_path",
                "chrome_profile_root",
                "text_to_voice_root",
                "text_to_voice_python",
                "chrome_exe_path",
                "veo3_root",
                "final_video_character_path",
            }:
                browse = QToolButton()
                browse.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DirOpenIcon))
                browse.setFixedSize(34, 34)
                browse.clicked.connect(lambda _=False, k=key: self.browse_setting_path(k))
                row.addWidget(browse)
            form.addRow(label, row)
        scroll_layout.addWidget(form_box)
        scroll_layout.addStretch(1)
        scroll.setWidget(scroll_content)
        layout.addWidget(scroll, 1)
        save_row = QHBoxLayout()
        save_row.addStretch(1)
        save_btn = QPushButton("Lưu cấu hình")
        save_btn.setObjectName("PrimaryButton")
        save_btn.clicked.connect(self.save_settings_from_ui)
        save_row.addWidget(save_btn)
        layout.addLayout(save_row)
        return page

    def _apply_style(self) -> None:
        QApplication.instance().setStyle("Fusion")  # type: ignore[union-attr]
        base_font = QFont("Segoe UI", 10)
        QApplication.instance().setFont(base_font)  # type: ignore[union-attr]
        self.setStyleSheet(
            """
            QMainWindow, QWidget { background: #f7f8fb; color: #111827; }
            #AppTitle { font-size: 24px; font-weight: 750; color: #0f172a; }
            #Subtitle { color: #64748b; font-size: 12px; }
            #ProjectLabel { padding: 10px 12px; border: 1px solid #dbe4ea; border-radius: 8px; background: #ffffff; color: #334155; }
            #ResultsControls { background: #ffffff; border: 1px solid #dbe4ea; border-radius: 8px; }
            QLabel#ActionSectionLabel { background: #eef2f7; color: #334155; border-radius: 6px; padding: 8px 10px; font-weight: 750; }
            #SidePanel, #SidePanelContent, QScrollArea { background: #f7f8fb; border: none; }
            QGroupBox { background: #ffffff; border: 1px solid #dbe4ea; border-radius: 8px; margin-top: 12px; font-weight: 650; }
            QGroupBox::title { subcontrol-origin: margin; left: 12px; padding: 0 5px; color: #0f172a; }
            QLabel#FieldLabel { background: transparent; color: #475569; font-size: 11px; font-weight: 650; padding-top: 2px; }
            QLineEdit, QTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
                background: #ffffff; border: 1px solid #cbd5e1; border-radius: 6px; padding: 7px 8px; min-height: 22px;
                selection-background-color: #0f766e; selection-color: #ffffff;
            }
            QTextEdit#LogBox { background: #101827; color: #d1e7e0; border-radius: 8px; font-family: Consolas, monospace; font-size: 10px; }
            QPushButton {
                background: #ffffff; border: 1px solid #cbd5e1; border-radius: 7px; padding: 8px 12px; font-weight: 600;
            }
            QPushButton:hover { background: #eef6f5; border-color: #5eead4; }
            QPushButton:disabled { color: #94a3b8; background: #f1f5f9; border-color: #e2e8f0; }
            QPushButton#PrimaryButton { background: #0f766e; color: #ffffff; border-color: #0f766e; }
            QPushButton#PrimaryButton:hover { background: #115e59; border-color: #115e59; }
            QTabWidget::pane { border: 1px solid #dbe4ea; background: #ffffff; border-radius: 8px; }
            QTabBar::tab { padding: 9px 16px; margin-right: 4px; border: 1px solid #dbe4ea; border-bottom: none; border-top-left-radius: 7px; border-top-right-radius: 7px; background: #eef2f7; }
            QTabBar::tab:selected { background: #ffffff; color: #0f766e; font-weight: 700; }
            QTableWidget { background: #ffffff; alternate-background-color: #f8fafc; border: 1px solid #dbe4ea; border-radius: 8px; gridline-color: #eef2f7; }
            QHeaderView::section { background: #f1f5f9; color: #334155; border: none; border-right: 1px solid #e2e8f0; padding: 8px; font-weight: 700; }
            QTableWidget::item { padding: 8px; }
            QToolButton { background: #ffffff; border: 1px solid #cbd5e1; border-radius: 6px; padding: 5px; }
            QToolButton:hover { background: #eef6f5; border-color: #5eead4; }
            """
        )

    def select_combo_data(self, combo: QComboBox, value: str) -> None:
        for index in range(combo.count()):
            if str(combo.itemData(index)) == str(value):
                combo.setCurrentIndex(index)
                return

    def populate_auto_story_genres(self) -> None:
        if not hasattr(self, "auto_genre_combo"):
            return
        self.auto_genre_combo.clear()
        for key, label in AUTO_STORY_GENRES:
            self.auto_genre_combo.addItem(label, key)
            if key in PRIORITY_AUTO_STORY_GENRES:
                index = self.auto_genre_combo.count() - 1
                self.auto_genre_combo.setItemData(index, QBrush(QColor("#dcfce7")), Qt.ItemDataRole.BackgroundRole)
                self.auto_genre_combo.setItemData(index, QBrush(QColor("#14532d")), Qt.ItemDataRole.ForegroundRole)

    def apply_auto_story_genre_preset(self) -> None:
        if not hasattr(self, "auto_genre_combo"):
            return
        preset = AUTO_STORY_GENRE_PRESETS.get(str(self.auto_genre_combo.currentData() or ""), {})
        mapping = (
            ("tone", "auto_tone_combo"),
            ("narrator", "auto_narrator_combo"),
            ("hook", "auto_hook_combo"),
            ("cliffhanger", "auto_cliffhanger_combo"),
        )
        for key, attr in mapping:
            combo = getattr(self, attr, None)
            if not combo:
                continue
            self.select_combo_data(combo, str(preset.get(key) or ""))

    def populate_background_mode_combo(self, selected_mode: str | None = None) -> None:
        if not hasattr(self, "background_mode_combo"):
            return
        selected = str(selected_mode or self.background_mode_combo.currentData() or "cooking")
        self.background_mode_combo.blockSignals(True)
        self.background_mode_combo.clear()
        for mode, label, _last_used in background_video_mode_options():
            self.background_mode_combo.addItem(label, mode)
        self.select_combo_data(self.background_mode_combo, selected)
        self.background_mode_combo.blockSignals(False)

    def reload_text_to_voice_controls(self) -> None:
        if not hasattr(self, "ttv_language_combo"):
            return
        self.select_combo_data(self.ttv_language_combo, str(self.settings.get("text_to_voice_language") or "a"))
        self.reload_text_to_voice_voice_choices()
        self.select_combo_data(self.ttv_delivery_combo, str(self.settings.get("text_to_voice_delivery") or "dramatic"))
        self.ttv_speed_input.setText(str(self.settings.get("text_to_voice_speed") or "1.0"))

    def reload_text_to_voice_voice_choices(self) -> None:
        if not hasattr(self, "ttv_voice_combo"):
            return
        language = str(self.ttv_language_combo.currentData() or self.settings.get("text_to_voice_language") or "a")
        current = self.ttv_voice_combo.currentText().strip() or str(self.settings.get("text_to_voice_voice") or "")
        voices = VOICES.get(language, VOICES["a"])
        preferred = current if current in voices else str(self.settings.get("text_to_voice_voice") or "")
        if preferred not in voices:
            preferred = voices[0] if voices else "af_heart"

        self.ttv_voice_combo.blockSignals(True)
        self.ttv_voice_combo.clear()
        self.ttv_voice_combo.addItems(voices)
        index = self.ttv_voice_combo.findText(preferred, Qt.MatchFlag.MatchFixedString)
        if index < 0 and preferred:
            self.ttv_voice_combo.insertItem(0, preferred)
            index = 0
        self.ttv_voice_combo.setCurrentIndex(max(0, index))
        self.ttv_voice_combo.blockSignals(False)

    def reset_timeline(self, content_mode: str | None = None, chapter_count: int | None = None) -> None:
        if not hasattr(self, "timeline_table"):
            return
        self.timeline_table.setRowCount(0)
        self.chapter_rows.clear()
        workflow = self.load_workflow_for_ui(content_mode)
        if isinstance(workflow.get("steps"), list):
            for step in workflow.get("steps") or []:
                if not isinstance(step, dict):
                    continue
                output_id = str(step.get("output") or step.get("id") or "step")
                match = re.fullmatch(r"chapter_(\d+)", output_id)
                if chapter_count and match and int(match.group(1)) > int(chapter_count):
                    continue
                row = self.timeline_table.rowCount()
                self.timeline_table.insertRow(row)
                step_id = str(step.get("id") or step.get("output") or "step")
                label = str(step.get("name") or step_id)
                self.timeline_table.setItem(row, 0, QTableWidgetItem(label))
                self.timeline_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, step_id)
                self.set_status_item(row, 1, "waiting")
                if match:
                    index = int(match.group(1))
                    self.chapter_rows[index] = row
                    self.set_status_item(row, 2, "waiting")
                else:
                    self.set_status_item(row, 2, "")
                self.timeline_table.setItem(row, 3, QTableWidgetItem(""))
            return
        for step_id, label in [("story_bible", "Story bible"), ("outline", "Outline")]:
            row = self.timeline_table.rowCount()
            self.timeline_table.insertRow(row)
            self.timeline_table.setItem(row, 0, QTableWidgetItem(label))
            self.set_status_item(row, 1, "waiting")
            self.set_status_item(row, 2, "")
            self.timeline_table.setItem(row, 3, QTableWidgetItem(""))
            self.timeline_table.item(row, 0).setData(Qt.ItemDataRole.UserRole, step_id)
        count = 8
        for index in range(1, count + 1):
            row = self.timeline_table.rowCount()
            self.timeline_table.insertRow(row)
            self.chapter_rows[index] = row
            self.timeline_table.setItem(row, 0, QTableWidgetItem(f"Chapter {index:02d}"))
            self.set_status_item(row, 1, "waiting")
            self.set_status_item(row, 2, "waiting")
            self.timeline_table.setItem(row, 3, QTableWidgetItem(""))

    def set_status_item(self, row: int, col: int, status: str) -> None:
        label, bg, fg = STATUS_COLORS.get(status, STATUS_COLORS[""])
        item = QTableWidgetItem(label)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        item.setBackground(QColor(bg))
        item.setForeground(QColor(fg))
        self.timeline_table.setItem(row, col, item)

    def collect_input(self) -> dict:
        link = self.url_input.text().strip()
        try:
            voice_speed = max(0.5, min(float(self.ttv_speed_input.text().strip() or "1.0"), 2.0))
        except Exception:
            voice_speed = 1.0
        content_mode = str(self.content_mode_combo.currentData() or "growth_30") if hasattr(self, "content_mode_combo") else "growth_30"
        background_video_mode = str(self.background_mode_combo.currentData() or "cooking") if hasattr(self, "background_mode_combo") else "cooking"
        preset = content_preset(content_mode)
        ttv_language = str(self.ttv_language_combo.currentData() or "a") if hasattr(self, "ttv_language_combo") else "a"
        ttv_voice = self.ttv_voice_combo.currentText().strip() if hasattr(self, "ttv_voice_combo") else "af_heart"
        ttv_delivery = str(self.ttv_delivery_combo.currentData() or "dramatic") if hasattr(self, "ttv_delivery_combo") else "dramatic"
        return {
            "original_title": self.title_input.text().strip() or "story",
            "source_url": link,
            "youtube_link": link,
            "original_script": self.source_input.toPlainText().strip(),
            "original_thumb_text": self.thumb_text_input.toPlainText().strip(),
            "thumbnail_path": self.thumbnail_path_input.text().strip(),
            "character_image_path": self.character_path_input.text().strip() if hasattr(self, "character_path_input") else "",
            "target_audience": self.audience_input.text().strip() or "US audience",
            "rewrite_ratio": self.rewrite_ratio_input.text().strip() or "35%",
            "channel_name": self.channel_input.text().strip() or "Wise Woman Revenge",
            "output_language": self.language_input.currentText().strip() or "English",
            "content_mode": content_mode,
            "background_video_mode": background_video_mode,
            "content_mode_label": str(preset["label"]),
            "expected_duration": str(preset["expected_duration"]),
            "target_word_count": int(preset["target_word_count"]),
            "chapter_count": int(preset["chapter_count"]),
            "text_to_voice_language": ttv_language,
            "text_to_voice_voice": ttv_voice or "af_heart",
            "text_to_voice_delivery": ttv_delivery,
            "text_to_voice_speed": voice_speed,
        }

    def collect_auto_story_input(self) -> dict:
        try:
            voice_speed = max(0.5, min(float(self.ttv_speed_input.text().strip() or "1.0"), 2.0))
        except Exception:
            voice_speed = 1.0
        length_key = str(self.auto_length_combo.currentData() or "45")
        length = next((item for item in AUTO_STORY_LENGTHS if item[0] == length_key), AUTO_STORY_LENGTHS[1])
        _key, _label, chapter_count, target_word_count, expected_duration = length
        genre_key = str(self.auto_genre_combo.currentData() or "mystery")
        genre_label = self.auto_genre_combo.currentText().strip()
        setting_key, setting_prompt = self.next_auto_story_setting(genre_key)
        title = self.auto_title_input.text().strip() or f"Auto Story {genre_label.split(' - ', 1)[0]}"
        ttv_language = str(self.ttv_language_combo.currentData() or "a") if hasattr(self, "ttv_language_combo") else "a"
        ttv_voice = self.ttv_voice_combo.currentText().strip() if hasattr(self, "ttv_voice_combo") else "af_heart"
        ttv_delivery = str(self.ttv_delivery_combo.currentData() or "dramatic") if hasattr(self, "ttv_delivery_combo") else "dramatic"
        background_video_mode = str(self.background_mode_combo.currentData() or "cooking") if hasattr(self, "background_mode_combo") else "cooking"
        return {
            "original_title": title,
            "source_url": "",
            "youtube_link": "",
            "original_script": "",
            "original_thumb_text": "",
            "thumbnail_path": "",
            "character_image_path": self.character_path_input.text().strip() if hasattr(self, "character_path_input") else "",
            "target_audience": f"US audience age {self.auto_age_combo.currentText().strip()}, listeners 35+",
            "rewrite_ratio": "new original story",
            "channel_name": self.channel_input.text().strip() or "Wise Woman Revenge",
            "output_language": "English",
            "content_mode": "auto_story",
            "background_video_mode": background_video_mode,
            "content_mode_label": "Auto Story",
            "expected_duration": expected_duration,
            "target_word_count": int(target_word_count),
            "chapter_count": int(chapter_count),
            "auto_genre": genre_key,
            "auto_genre_label": genre_label,
            "auto_setting_key": setting_key,
            "auto_setting_prompt": setting_prompt,
            "auto_age_segment": self.auto_age_combo.currentText().strip(),
            "auto_episode_length": length_key,
            "auto_tone": str(self.auto_tone_combo.currentData() or self.auto_tone_combo.currentText()).strip(),
            "auto_tone_label": self.auto_tone_combo.currentText().strip(),
            "auto_narrator_style": str(self.auto_narrator_combo.currentData() or self.auto_narrator_combo.currentText()).strip(),
            "auto_narrator_style_label": self.auto_narrator_combo.currentText().strip(),
            "auto_hook_type": str(self.auto_hook_combo.currentData() or self.auto_hook_combo.currentText()).strip(),
            "auto_hook_type_label": self.auto_hook_combo.currentText().strip(),
            "auto_cliffhanger_level": str(self.auto_cliffhanger_combo.currentData() or self.auto_cliffhanger_combo.currentText()).strip(),
            "auto_cliffhanger_level_label": self.auto_cliffhanger_combo.currentText().strip(),
            "auto_seed_idea": self.auto_seed_input.toPlainText().strip(),
            "text_to_voice_language": ttv_language,
            "text_to_voice_voice": ttv_voice or "af_heart",
            "text_to_voice_delivery": ttv_delivery,
            "text_to_voice_speed": voice_speed,
        }

    def next_auto_story_setting(self, genre_key: str) -> tuple[str, str]:
        genre = str(genre_key or "").strip()
        lanes = AUTO_STORY_SETTING_ROTATIONS.get(genre) or []
        if not lanes:
            return "", ""
        path = Path(str(self.settings.get("projects_dir") or APP_DIR / "Projects")) / "auto_story_setting_rotation.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = {}
        last_index = int(data.get(f"{genre}_last_index", -1))
        next_index = (last_index + 1) % len(lanes)
        key, prompt = lanes[next_index]
        data[f"{genre}_last_index"] = next_index
        data[f"{genre}_last_key"] = key
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        return key, prompt

    def apply_input_data(self, data: dict) -> None:
        if not isinstance(data, dict):
            return

        self.title_input.setText(str(data.get("original_title") or ""))
        self.url_input.setText(str(data.get("source_url") or data.get("youtube_link") or ""))
        self.audience_input.setText(str(data.get("target_audience") or "US audience, long-form YouTube listeners"))
        self.rewrite_ratio_input.setText(str(data.get("rewrite_ratio") or "35%"))
        self.channel_input.setText(str(data.get("channel_name") or "Wise Woman Revenge"))
        self.source_input.setPlainText(str(data.get("original_script") or ""))
        self.thumb_text_input.setPlainText(str(data.get("original_thumb_text") or ""))
        self.thumbnail_path_input.setText(str(data.get("thumbnail_path") or ""))
        if hasattr(self, "character_path_input"):
            self.character_path_input.setText(str(data.get("character_image_path") or data.get("final_video_character_path") or ""))

        output_language = str(data.get("output_language") or "English").strip()
        if output_language:
            self.language_input.setCurrentText(output_language)

        content_mode = str(data.get("content_mode") or "growth_30").strip()
        if hasattr(self, "content_mode_combo"):
            self.select_combo_data(self.content_mode_combo, content_mode)

        background_video_mode = str(data.get("background_video_mode") or self.settings.get("background_video_mode") or "cooking").strip()
        if hasattr(self, "background_mode_combo"):
            self.populate_background_mode_combo(background_video_mode)

        language = str(data.get("text_to_voice_language") or self.settings.get("text_to_voice_language") or "a")
        self.select_combo_data(self.ttv_language_combo, language)
        self.reload_text_to_voice_voice_choices()

        voice = str(data.get("text_to_voice_voice") or self.settings.get("text_to_voice_voice") or "af_heart").strip()
        if voice:
            index = self.ttv_voice_combo.findText(voice, Qt.MatchFlag.MatchFixedString)
            if index < 0:
                self.ttv_voice_combo.insertItem(0, voice)
                index = 0
            self.ttv_voice_combo.setCurrentIndex(index)

        delivery = str(data.get("text_to_voice_delivery") or self.settings.get("text_to_voice_delivery") or "dramatic")
        self.select_combo_data(self.ttv_delivery_combo, delivery)
        self.ttv_speed_input.setText(str(data.get("text_to_voice_speed") or self.settings.get("text_to_voice_speed") or "1.0"))

    @staticmethod
    def input_has_content(data: dict) -> bool:
        if not isinstance(data, dict):
            return False
        title = str(data.get("original_title") or "").strip()
        if title and title.lower() != "story":
            return True
        for key in ("source_url", "youtube_link", "original_script", "original_thumb_text", "thumbnail_path", "character_image_path"):
            if str(data.get(key) or "").strip():
                return True
        return False

    def restore_ui_state(self) -> None:
        draft = self.ui_state.get("draft_input") if isinstance(self.ui_state, dict) else {}
        selected = str(self.ui_state.get("selected_project") or "").strip() if isinstance(self.ui_state, dict) else ""
        selected = rebase_app_path(selected, require_exists=True)
        if (not self.input_has_content(draft)) and selected and Path(selected).exists():
            project_input = self.project_store.load_input(Path(selected))
            if self.input_has_content(project_input):
                draft = project_input
        if not isinstance(draft, dict):
            return
        self._restoring_ui_state = True
        try:
            self.apply_input_data(draft)
        finally:
            self._restoring_ui_state = False

    def save_current_ui_state(self) -> None:
        if self._restoring_ui_state or not hasattr(self, "title_input"):
            return
        state = dict(self.ui_state) if isinstance(self.ui_state, dict) else {}
        try:
            draft = self.collect_input()
        except Exception:
            draft = state.get("draft_input") or {}
        if self.input_has_content(draft) or not self.input_has_content(state.get("draft_input") or {}):
            state["draft_input"] = draft
        if hasattr(self, "project_combo"):
            selected = self.project_combo.currentData()
            if selected:
                state["selected_project"] = str(selected)
        self.ui_state = state
        try:
            save_ui_state(state)
        except Exception:
            pass

    def validate_input(self, data: dict) -> bool:
        if str(data.get("content_mode") or "") == "auto_story":
            return True
        if not data["original_script"]:
            QMessageBox.warning(self, "Thiếu transcript", "Hãy dán transcript / nội dung gốc trước khi chạy.")
            return False
        workflow = self.load_workflow_for_ui()
        needs_thumb = any(isinstance(step, dict) and step.get("image") == "thumbnail" for step in workflow.get("steps", []))
        if needs_thumb and not data.get("original_thumb_text") and not (data.get("thumbnail_path") and Path(data["thumbnail_path"]).exists()):
            QMessageBox.warning(self, "Thiếu thumbnail", "Workflow có bước trích text thumbnail. Hãy chọn ảnh thumbnail hoặc dán sẵn text thumbnail.")
            return False
        return True

    def load_workflow_for_ui(self, content_mode: str | None = None) -> dict:
        try:
            mode = str(content_mode or (self.content_mode_combo.currentData() if hasattr(self, "content_mode_combo") else "") or "")
            preset = content_preset(mode)
            preset_path = Path(__file__).resolve().parents[1] / "workflows" / str(preset["workflow"])
            path = preset_path if preset_path.exists() else Path(str(self.settings.get("workflow_path") or ""))
            if path.exists():
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {}

    def start_pipeline(self) -> None:
        if self.pipeline and self.pipeline.isRunning():
            return
        self.settings = load_settings()
        self.project_store = ProjectStore(self.settings["projects_dir"])
        data = self.collect_input()
        if not self.validate_input(data):
            return
        self.save_current_ui_state()
        self.settings["text_to_voice_language"] = data["text_to_voice_language"]
        self.settings["text_to_voice_voice"] = data["text_to_voice_voice"]
        self.settings["text_to_voice_delivery"] = data["text_to_voice_delivery"]
        self.settings["text_to_voice_speed"] = data["text_to_voice_speed"]
        save_settings(self.settings)
        self.reset_timeline()
        self.log_box.clear()
        self.current_project = None
        self.run_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.pipeline = PipelineWorker(self.settings, data, self)
        self.pipeline.log_message.connect(self.add_log)
        self.pipeline.project_created.connect(self.on_project_created)
        self.pipeline.step_status.connect(self.on_step_status)
        self.pipeline.chapter_text_status.connect(self.on_chapter_text_status)
        self.pipeline.chapter_voice_status.connect(self.on_chapter_voice_status)
        self.pipeline.finished.connect(self.on_pipeline_finished)
        self.pipeline.start()

    def start_auto_story_pipeline(self) -> None:
        if self.pipeline and self.pipeline.isRunning():
            return
        self.settings = load_settings()
        self.project_store = ProjectStore(self.settings["projects_dir"])
        data = self.collect_auto_story_input()
        if not self.validate_input(data):
            return
        self.settings["text_to_voice_language"] = data["text_to_voice_language"]
        self.settings["text_to_voice_voice"] = data["text_to_voice_voice"]
        self.settings["text_to_voice_delivery"] = data["text_to_voice_delivery"]
        self.settings["text_to_voice_speed"] = data["text_to_voice_speed"]
        save_settings(self.settings)
        self.auto_story_render_after_pipeline = bool(
            getattr(self, "auto_full_video_check", None) and self.auto_full_video_check.isChecked()
        )
        self.reset_timeline("auto_story", int(data.get("chapter_count") or 6))
        self.log_box.clear()
        self.current_project = None
        self.run_button.setEnabled(False)
        if hasattr(self, "auto_story_run_button"):
            self.auto_story_run_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.pipeline = PipelineWorker(self.settings, data, self)
        self.pipeline.log_message.connect(self.add_log)
        self.pipeline.project_created.connect(self.on_project_created)
        self.pipeline.step_status.connect(self.on_step_status)
        self.pipeline.chapter_text_status.connect(self.on_chapter_text_status)
        self.pipeline.chapter_voice_status.connect(self.on_chapter_voice_status)
        self.pipeline.finished.connect(self.on_pipeline_finished)
        self.pipeline.start()

    def stop_pipeline(self) -> None:
        if self.pipeline and self.pipeline.isRunning():
            self.add_log("Đang yêu cầu dừng pipeline...")
            self.pipeline.stop()
            self.stop_button.setEnabled(False)

    def open_chrome(self) -> None:
        if self.chrome_thread and self.chrome_thread.isRunning():
            return
        self.settings = load_settings()
        self.chrome_button.setEnabled(False)
        self.chrome_thread = ChromeOpenThread(self.settings, self)
        self.chrome_thread.log.connect(self.add_log)
        self.chrome_thread.done.connect(self.on_chrome_opened)
        self.chrome_thread.start()

    def on_chrome_opened(self, ok: bool, message: str) -> None:
        self.chrome_button.setEnabled(True)
        self.add_log(message)
        if not ok:
            QMessageBox.warning(self, "Chrome", message)

    def open_text_to_voice(self) -> None:
        if self.text_to_voice_thread and self.text_to_voice_thread.isRunning():
            return
        self.settings = load_settings()
        self.text_to_voice_button.setEnabled(False)
        self.text_to_voice_thread = TextToVoiceOpenThread(self.settings, self)
        self.text_to_voice_thread.log.connect(self.add_log)
        self.text_to_voice_thread.done.connect(self.on_text_to_voice_opened)
        self.text_to_voice_thread.start()

    def on_text_to_voice_opened(self, ok: bool, message: str) -> None:
        self.text_to_voice_button.setEnabled(True)
        if not ok:
            self.add_log(message)
            QMessageBox.warning(self, "Text to Voice", message)
            return
        self.add_log(f"Text to Voice UI da san sang: {message}")
        QDesktopServices.openUrl(QUrl(message))

    def on_project_created(self, path: str) -> None:
        self.current_project = Path(path)
        self.project_label.setText(f"Project hien tai: {self.current_project}")
        self.reload_projects(select_path=self.current_project)

    def on_step_status(self, step_id: str, status: str, detail: str) -> None:
        for row in range(self.timeline_table.rowCount()):
            item = self.timeline_table.item(row, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == step_id:
                self.set_status_item(row, 1, status)
                if detail:
                    self.timeline_table.setItem(row, 3, QTableWidgetItem(detail))
                return

    def on_chapter_text_status(self, index: int, status: str, detail: str) -> None:
        row = self.chapter_rows.get(int(index))
        if row is None:
            return
        self.set_status_item(row, 1, status)
        if detail:
            self.timeline_table.setItem(row, 3, QTableWidgetItem(detail))
            self.load_selected_project_results()

    def on_chapter_voice_status(self, index: int, status: str, detail: str) -> None:
        row = self.chapter_rows.get(int(index))
        if row is None:
            return
        self.set_status_item(row, 2, status)
        if detail and status == "done":
            existing = self.timeline_table.item(row, 3).text() if self.timeline_table.item(row, 3) else ""
            combined = existing if existing else detail
            if existing and detail not in existing:
                combined = f"{existing} | {detail}"
            self.timeline_table.setItem(row, 3, QTableWidgetItem(combined))
        self.load_selected_project_results()

    def on_pipeline_finished(self, ok: bool, message: str) -> None:
        self.run_button.setEnabled(True)
        if hasattr(self, "auto_story_run_button"):
            self.auto_story_run_button.setEnabled(True)
        self.stop_button.setEnabled(False)
        self.add_log(message)
        self.reload_projects(select_path=self.current_project)
        if ok and self.auto_story_render_after_pipeline and self.current_project:
            self.auto_story_render_after_pipeline = False
            self.start_auto_story_full_video(self.current_project)

    def start_auto_story_full_video(self, project_dir: Path) -> None:
        if self.auto_story_full_thread and self.auto_story_full_thread.isRunning():
            return
        self.settings = load_settings()
        if hasattr(self, "background_mode_combo"):
            self.settings["background_video_mode"] = str(self.background_mode_combo.currentData() or "cooking")
        if hasattr(self, "auto_story_run_button"):
            self.auto_story_run_button.setEnabled(False)
        self.veo3_auto_button.setEnabled(False)
        self.auto_edit_button.setEnabled(False)
        self.add_log(f"Auto Story: bat dau voice + edit video final cho project: {project_dir.name}")
        self.auto_story_full_thread = AutoStoryFullVideoThread(self.settings, project_dir, self)
        self.auto_story_full_thread.log.connect(self.add_log)
        self.auto_story_full_thread.done.connect(self.on_auto_story_full_video_finished)
        self.auto_story_full_thread.start()

    def on_auto_story_full_video_finished(self, ok: bool, message: str) -> None:
        if hasattr(self, "auto_story_run_button"):
            self.auto_story_run_button.setEnabled(True)
        self.veo3_auto_button.setEnabled(VEO3_IMAGE_GENERATION_ENABLED)
        self.auto_edit_button.setEnabled(True)
        self.reload_projects(select_path=self.current_project)
        if ok:
            self.add_log(f"Auto Story video final xong: {message}")
            QMessageBox.information(self, "Auto Story video xong", f"Da xuat video final:\n{message}")
            open_path(Path(message).parent)
            return
        self.add_log(f"Auto Story video loi: {message}")
        QMessageBox.warning(self, "Auto Story video loi", message)
        if ok:
            QMessageBox.information(self, "Hoàn tất", "Đã tạo xong kịch bản và audio Text to Voice cho từng chương.")
        else:
            QMessageBox.warning(self, "Pipeline dung / loi", message)

    def add_log(self, message: str) -> None:
        self.log_box.append(str(message))

    def reload_projects(self, select_path: Path | None = None) -> None:
        self.project_store = ProjectStore(self.settings["projects_dir"])
        previous = str(select_path or self.project_combo.currentData() or self.ui_state.get("selected_project") or "")
        previous = rebase_app_path(previous, require_exists=True)
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        for project_dir in self.project_store.list_projects():
            self.project_combo.addItem(project_dir.name, str(project_dir))
        if previous:
            for i in range(self.project_combo.count()):
                if self.project_combo.itemData(i) == previous:
                    self.project_combo.setCurrentIndex(i)
                    break
        self.project_combo.blockSignals(False)
        self.load_selected_project_results()

    def load_selected_project_results(self) -> None:
        raw = self.project_combo.currentData()
        if not raw:
            self.results_table.setRowCount(0)
            return
        project_dir = Path(str(raw))
        if not project_dir.exists():
            self.results_table.setRowCount(0)
            return
        if not (self.pipeline and self.pipeline.isRunning()):
            self.current_project = project_dir
            self.project_label.setText(f"Project hien tai: {project_dir}")
            project_input = self.project_store.load_input(project_dir)
            mode = str(project_input.get("content_mode") or "").strip()
            if mode and hasattr(self, "content_mode_combo"):
                self.content_mode_combo.blockSignals(True)
                self.select_combo_data(self.content_mode_combo, mode)
                self.content_mode_combo.blockSignals(False)
            self.reset_timeline()
            self.apply_project_state_to_timeline(project_dir)
        rows = self.project_store.collect_results(project_dir)
        self.results_table.setRowCount(0)
        for result in rows:
            row = self.results_table.rowCount()
            self.results_table.insertRow(row)
            self.results_table.setItem(row, 0, QTableWidgetItem(str(result.get("type") or "")))
            self.results_table.setItem(row, 1, QTableWidgetItem(str(result.get("name") or "")))
            text_path = str(result.get("text_path") or "")
            voice_path = str(result.get("voice_path") or "")
            self.results_table.setItem(row, 2, QTableWidgetItem("Có" if text_path and Path(text_path).exists() else "-"))
            self.results_table.setItem(row, 3, QTableWidgetItem("Có" if voice_path and Path(voice_path).exists() else "-"))
            self.results_table.setCellWidget(row, 4, self.action_widget(str(result.get("name") or ""), text_path, voice_path))
        self.results_table.resizeRowsToContents()
        self.save_current_ui_state()

    def apply_project_state_to_timeline(self, project_dir: Path) -> None:
        if not hasattr(self, "timeline_table") or not project_dir.exists():
            return
        state = self.project_store.load_state(project_dir)
        steps = state.get("steps") if isinstance(state.get("steps"), dict) else {}
        chapters = state.get("chapters") if isinstance(state.get("chapters"), dict) else {}

        for row in range(self.timeline_table.rowCount()):
            item = self.timeline_table.item(row, 0)
            step_id = str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""
            step = steps.get(step_id) if isinstance(steps, dict) else None
            if isinstance(step, dict) and step.get("status"):
                self.set_status_item(row, 1, str(step.get("status") or ""))
                detail = str(step.get("path") or "")
                if detail:
                    self.timeline_table.setItem(row, 3, QTableWidgetItem(detail))

        for key, chapter in chapters.items():
            if not isinstance(chapter, dict):
                continue
            try:
                index = int(str(key))
            except Exception:
                continue
            row = self.chapter_rows.get(index)
            if row is None:
                continue
            text_status = str(chapter.get("text_status") or "")
            voice_status = str(chapter.get("voice_status") or "")
            if text_status:
                self.set_status_item(row, 1, text_status)
            if voice_status:
                self.set_status_item(row, 2, voice_status)
            detail_parts = [str(chapter.get("text_path") or ""), str(chapter.get("voice_path") or "")]
            detail = " | ".join(part for part in detail_parts if part)
            if detail:
                self.timeline_table.setItem(row, 3, QTableWidgetItem(detail))

    def load_selected_project_into_form(self) -> None:
        project_dir = self.selected_project_dir()
        if not project_dir:
            QMessageBox.warning(self, "Chua chon project", "Hay chon project truoc.")
            return
        data = self.project_store.load_input(project_dir)
        if not data:
            QMessageBox.warning(self, "Khong co input", f"Khong doc duoc input.json trong:\n{project_dir}")
            return
        self.apply_input_data(data)
        self.current_project = project_dir
        self.project_label.setText(f"Project hien tai: {project_dir}")
        self.reset_timeline()
        self.apply_project_state_to_timeline(project_dir)
        self.save_current_ui_state()
        self.add_log(f"Da nap input tu project: {project_dir.name}")

    def import_prompt_file_as_project(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Chon file prompt anh",
            str(Path.home()),
            "Text files (*.txt);;All files (*.*)",
        )
        if not path:
            return
        source = Path(path)
        if not source.exists() or not source.is_file():
            QMessageBox.warning(self, "File prompt loi", f"Khong thay file:\n{source}")
            return

        project_dir = self.create_prompt_batch_project(source)
        QMessageBox.information(self, "Da nhap prompt", "Da tao project batch anh. Bam 'Dai: Tao anh' de chay hang loat.")

    def create_prompt_batch_project(self, source: Path) -> Path:
        self.settings = load_settings()
        self.project_store = ProjectStore(self.settings["projects_dir"])
        project_dir = self.project_store.create_project(
            {
                "original_title": source.stem or "prompt-batch",
                "source_url": "",
                "youtube_link": "",
                "original_script": "",
                "original_thumb_text": "",
                "thumbnail_path": "",
                "character_image_path": "",
                "prompt_source_path": str(source),
                "content_mode": str(self.content_mode_combo.currentData() or "growth_30") if hasattr(self, "content_mode_combo") else "growth_30",
            }
        )
        target = project_dir / "artifacts" / "veo3_prompts.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        self.reload_projects(select_path=project_dir)
        self.current_project = project_dir
        self.project_label.setText(f"Project hien tai: {project_dir}")
        self.add_log(f"Da nhap file prompt: {source.name} -> {target}")
        return project_dir

    def find_auto_prompt_file(self) -> Path | None:
        self.settings = load_settings()
        configured = str(self.settings.get("veo_prompt_source_file") or "").strip()
        candidates: list[Path] = []
        if configured:
            candidates.append(Path(rebase_app_path(configured, require_exists=True)))

        base_dirs = []
        for base in (Path.cwd(), APP_DIR.parent, APP_DIR):
            if base not in base_dirs:
                base_dirs.append(base)
        exact_names = (
            "promt chatgpt viet ra",
            "prompt chatgpt viet ra",
            "veo3_prompts.txt",
            "whisk_prompts.txt",
            "image_prompts_for_flow.txt",
        )
        for base in base_dirs:
            for name in exact_names:
                candidates.append(base / name)

        discovered: list[Path] = []
        for base in base_dirs:
            if not base.exists():
                continue
            for pattern in ("*promt*", "*prompt*", "*PROMPT*"):
                for item in base.glob(pattern):
                    if item.is_file():
                        discovered.append(item)
        discovered.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
        candidates.extend(discovered)

        seen: set[str] = set()
        for candidate in candidates:
            try:
                path = candidate.resolve()
            except Exception:
                path = candidate
            key = str(path).lower()
            if key in seen or not path.exists() or not path.is_file():
                continue
            seen.add(key)
            try:
                sample = path.read_text(encoding="utf-8", errors="ignore")[:4000]
            except Exception:
                continue
            if re.search(r"(?im)^\s*(?:prompt\s*)?\d{1,3}\s*[\).:\-]\s*\S", sample) or "cinematic" in sample.lower():
                return path
        return None

    def action_widget(self, name: str, text_path: str, voice_path: str) -> QWidget:
        box = QWidget()
        box.setFixedWidth(164)
        layout = QHBoxLayout(box)
        layout.setContentsMargins(4, 2, 4, 2)
        layout.setSpacing(5)
        view_btn = self.icon_button(QStyle.StandardPixmap.SP_FileDialogContentsView, "Xem text")
        copy_btn = self.icon_button(QStyle.StandardPixmap.SP_DialogSaveButton, "Copy text")
        play_btn = self.icon_button(QStyle.StandardPixmap.SP_MediaPlay, "Mở audio")
        folder_btn = self.icon_button(QStyle.StandardPixmap.SP_DirOpenIcon, "Mở thư mục")
        view_btn.clicked.connect(lambda: self.open_text_viewer(name, text_path))
        copy_btn.clicked.connect(lambda: self.copy_text_file(text_path))
        play_btn.clicked.connect(lambda: open_path(voice_path))
        folder_btn.clicked.connect(lambda: open_path(Path(text_path).parent if text_path else Path(voice_path).parent))
        view_btn.setEnabled(bool(text_path and Path(text_path).exists()))
        copy_btn.setEnabled(bool(text_path and Path(text_path).exists()))
        play_btn.setEnabled(bool(voice_path and Path(voice_path).exists()))
        folder_btn.setEnabled(bool((text_path and Path(text_path).exists()) or (voice_path and Path(voice_path).exists())))
        for button in (view_btn, copy_btn, play_btn, folder_btn):
            layout.addWidget(button)
        return box

    def icon_button(self, icon: QStyle.StandardPixmap, tip: str) -> QToolButton:
        button = QToolButton()
        button.setIcon(self.style().standardIcon(icon))
        button.setToolTip(tip)
        button.setFixedSize(34, 30)
        return button

    def open_text_viewer(self, name: str, text_path: str) -> None:
        if not text_path or not Path(text_path).exists():
            return
        viewer = TextViewer(name, text_path, self)
        self.viewer_windows.append(viewer)
        viewer.show()

    def copy_text_file(self, text_path: str) -> None:
        try:
            QApplication.clipboard().setText(Path(text_path).read_text(encoding="utf-8"))
            self.add_log(f"Đã copy text: {Path(text_path).name}")
        except Exception as exc:
            QMessageBox.warning(self, "Lỗi copy", str(exc))

    def open_selected_project_folder(self) -> None:
        raw = self.project_combo.currentData()
        if raw:
            open_path(raw)

    def selected_project_dir(self) -> Path | None:
        raw = self.project_combo.currentData()
        if not raw:
            return None
        project_dir = Path(str(raw))
        return project_dir if project_dir.exists() else None

    def open_selected_veo_folder(self) -> None:
        project_dir = self.selected_project_dir()
        if not project_dir:
            QMessageBox.warning(self, "Chưa chọn project", "Hãy chọn project trước.")
            return
        veo_dir = project_dir / "veo_videos"
        veo_dir.mkdir(parents=True, exist_ok=True)
        open_path(veo_dir)

    def open_selected_shorts_folder(self) -> None:
        project_dir = self.selected_project_dir()
        if not project_dir:
            QMessageBox.warning(self, "Chưa chọn project", "Hãy chọn project trước.")
            return
        shorts_dir = project_dir / "shorts"
        shorts_dir.mkdir(parents=True, exist_ok=True)
        open_path(shorts_dir)

    def start_merge_video(self) -> None:
        if self.video_merge_thread and self.video_merge_thread.isRunning():
            return
        project_dir = self.selected_project_dir()
        if not project_dir:
            QMessageBox.warning(self, "Chua chon project", "Hay chon project truoc.")
            return

        veo_dir = project_dir / "veo_videos"
        veo_dir.mkdir(parents=True, exist_ok=True)
        videos = collect_video_files(veo_dir)
        if not videos:
            QMessageBox.warning(self, "Thieu video VEO", f"Chua co video VEO trong:\n{veo_dir}")
            open_path(veo_dir)
            return

        self.settings = load_settings()
        self.merge_video_button.setEnabled(False)
        self.auto_edit_button.setEnabled(False)
        self.add_log(f"Bat dau ghep {len(videos)} video VEO cho project: {project_dir.name}")
        self.video_merge_thread = VideoMergeThread(self.settings, project_dir, self)
        self.video_merge_thread.log.connect(self.add_log)
        self.video_merge_thread.done.connect(self.on_merge_video_finished)
        self.video_merge_thread.start()

    def on_merge_video_finished(self, ok: bool, message: str) -> None:
        self.merge_video_button.setEnabled(True)
        self.auto_edit_button.setEnabled(True)
        if hasattr(self, "background_mode_combo"):
            self.populate_background_mode_combo(str(self.background_mode_combo.currentData() or "cooking"))
        if ok:
            self.add_log(f"Ghep video xong: {message}")
            QMessageBox.information(self, "Ghep video xong", f"Da ghep video:\n{message}")
            open_path(Path(message).parent)
            return
        self.add_log(f"Ghep video loi: {message}")
        QMessageBox.warning(self, "Ghep video loi", message)

    def start_final_render(self) -> None:
        if self.video_edit_thread and self.video_edit_thread.isRunning():
            return
        project_dir = self.selected_project_dir()
        if not project_dir:
            QMessageBox.warning(self, "Chua chon project", "Hay chon project truoc.")
            return

        veo_dir = project_dir / "veo_videos"
        veo_dir.mkdir(parents=True, exist_ok=True)
        voices = collect_voice_files(project_dir)
        if not voices:
            QMessageBox.warning(self, "Thieu voice", f"Project chua co file voice trong:\n{project_dir / 'voices'}")
            return

        self.settings = load_settings()
        if hasattr(self, "background_mode_combo"):
            self.settings["background_video_mode"] = str(self.background_mode_combo.currentData() or "cooking")
        self.auto_edit_button.setEnabled(False)
        self.add_log(f"Bat dau tao voice + edit final cho project: {project_dir.name}")
        # VEO3 image generation is disabled for now; render_project_video will use
        # available project/background media instead of requiring VEO images here.
        self.video_edit_thread = VideoEditThread(self.settings, project_dir, self, background_files=None)
        self.video_edit_thread.log.connect(self.add_log)
        self.video_edit_thread.done.connect(self.on_auto_edit_finished)
        self.video_edit_thread.start()

    def start_auto_edit_video(self) -> None:
        if self.video_edit_thread and self.video_edit_thread.isRunning():
            return
        project_dir = self.selected_project_dir()
        if not project_dir:
            QMessageBox.warning(self, "Chưa chọn project", "Hãy chọn project trước.")
            return

        veo_dir = project_dir / "veo_videos"
        veo_dir.mkdir(parents=True, exist_ok=True)
        images = collect_story_image_files(project_dir, veo_dir)
        voices = collect_voice_files(project_dir)
        if not voices:
            QMessageBox.warning(self, "Thiếu voice", f"Project chưa có file voice trong:\n{project_dir / 'voices'}")
            return
        if not images:
            QMessageBox.warning(
                self,
                "Thieu anh nen",
                f"Chua co anh VEO. Bam 'Dai: Tao anh' truoc hoac bo file .jpg/.png/.webp vao:\n{veo_dir / 'image'}",
            )
            open_path(veo_dir)
            return

        self.settings = load_settings()
        if hasattr(self, "background_mode_combo"):
            self.settings["background_video_mode"] = str(self.background_mode_combo.currentData() or "cooking")
        self.auto_edit_button.setEnabled(False)
        self.add_log(f"Bat dau auto edit anh + voice cho project: {project_dir.name}")
        self.video_edit_thread = VideoEditThread(self.settings, project_dir, self, background_files=images)
        self.video_edit_thread.log.connect(self.add_log)
        self.video_edit_thread.done.connect(self.on_auto_edit_finished)
        self.video_edit_thread.start()

    def on_auto_edit_finished(self, ok: bool, message: str) -> None:
        self.auto_edit_button.setEnabled(True)
        if hasattr(self, "background_mode_combo"):
            self.populate_background_mode_combo(str(self.background_mode_combo.currentData() or "cooking"))
        if ok:
            self.add_log(f"Auto edit xong: {message}")
            QMessageBox.information(self, "Auto edit xong", f"Da xuat video final:\n{message}")
            open_path(Path(message).parent)
            return
        self.add_log(f"Auto edit loi: {message}")
        QMessageBox.warning(self, "Auto edit loi", message)

    def login_veo3(self) -> None:
        if self.veo3_login_thread and self.veo3_login_thread.isRunning():
            return
        self.settings = load_settings()
        if not str(self.settings.get("veo3_email") or "").strip() or not str(self.settings.get("veo3_password") or "").strip():
            QMessageBox.warning(self, "Thiếu VEO3", "Nhập VEO3 email/password trong tab Cấu hình trước.")
            return
        self.veo3_login_button.setEnabled(False)
        self.add_log("Bắt đầu đăng nhập VEO3 / lấy token Google Labs...")
        self.veo3_login_thread = Veo3LoginThread(self.settings, self)
        self.veo3_login_thread.log.connect(self.add_log)
        self.veo3_login_thread.done.connect(self.on_veo3_login_finished)
        self.veo3_login_thread.start()

    def on_veo3_login_finished(self, ok: bool, message: str) -> None:
        self.veo3_login_button.setEnabled(True)
        self.add_log(f"VEO3 login: {message}")
        if ok:
            QMessageBox.information(self, "VEO3", message)
        else:
            QMessageBox.warning(self, "VEO3", message)

    def start_veo3_generate(self) -> None:
        if not VEO3_IMAGE_GENERATION_ENABLED:
            self.add_log("Tao anh VEO3 dang tam tat. Dung nut Voice + edit de render video.")
            return
        if self.veo3_generate_thread and self.veo3_generate_thread.isRunning():
            return
        project_dir = self.selected_project_dir()
        if not project_dir:
            prompt_file = self.find_auto_prompt_file()
            if not prompt_file:
                QMessageBox.warning(
                    self,
                    "Chua co prompt",
                    "Khong thay project hoac file prompt de tao anh. Dat file prompt trong thu muc master tool, vi du: promt chatgpt viet ra, roi bam lai.",
                )
                return
            project_dir = self.create_prompt_batch_project(prompt_file)
            self.add_log(f"Tu dong tao project batch anh tu file prompt: {prompt_file}")

        self.settings = load_settings()
        self.veo3_auto_button.setEnabled(False)
        self.merge_video_button.setEnabled(False)
        self.auto_edit_button.setEnabled(False)
        self.shorts_veo_button.setEnabled(False)
        self.add_log(f"Bat dau tao anh cho video dai: {project_dir.name}")
        self.veo3_generate_thread = Veo3GenerateThread(self.settings, project_dir, self)
        self.veo3_generate_thread.log.connect(self.add_log)
        self.veo3_generate_thread.done.connect(self.on_veo3_generate_finished)
        self.veo3_generate_thread.start()

    def on_veo3_generate_finished(self, ok: bool, message: str) -> None:
        self.veo3_auto_button.setEnabled(VEO3_IMAGE_GENERATION_ENABLED)
        self.merge_video_button.setEnabled(True)
        self.auto_edit_button.setEnabled(True)
        self.shorts_veo_button.setEnabled(VEO3_IMAGE_GENERATION_ENABLED)
        if ok:
            self.add_log(f"Tao anh video dai xong: {message}")
            QMessageBox.information(self, "Tao anh xong", f"Da tao anh:\n{message}")
            project_dir = self.selected_project_dir()
            if project_dir:
                open_path(project_dir / "veo_videos" / "image")
            return
        self.add_log(f"Tao anh video dai loi: {message}")
        QMessageBox.warning(self, "Tao anh loi", message)

    def start_shorts_veo_generate(self) -> None:
        if not VEO3_IMAGE_GENERATION_ENABLED:
            self.add_log("Tao anh VEO3 Shorts dang tam tat.")
            return
        if self.shorts_veo_thread and self.shorts_veo_thread.isRunning():
            return
        if self.veo3_generate_thread and self.veo3_generate_thread.isRunning():
            QMessageBox.warning(self, "Dang tao anh", "Hay doi luong tao anh video dai chay xong truoc.")
            return
        project_dir = self.selected_project_dir()
        if not project_dir:
            QMessageBox.warning(self, "Chua chon project", "Hay chon project truoc.")
            return

        shorts_dir = project_dir / "shorts"
        package_path = shorts_dir / "shorts_package.json"
        if not package_path.exists():
            QMessageBox.warning(
                self,
                "Chua co Shorts package",
                f"Chua co shorts_package.json trong:\n{shorts_dir}\n\nHay chay workflow tao kich ban truoc.",
            )
            return

        self.settings = load_settings()
        self.shorts_veo_button.setEnabled(False)
        self.shorts_render_button.setEnabled(False)
        self.veo3_auto_button.setEnabled(False)
        self.add_log(f"Bat dau tao anh Shorts 9:16 cho project: {project_dir.name}")
        self.shorts_veo_thread = ShortsVeoGenerateThread(self.settings, project_dir, self)
        self.shorts_veo_thread.log.connect(self.add_log)
        self.shorts_veo_thread.done.connect(self.on_shorts_veo_generate_finished)
        self.shorts_veo_thread.start()

    def on_shorts_veo_generate_finished(self, ok: bool, message: str) -> None:
        self.shorts_veo_button.setEnabled(VEO3_IMAGE_GENERATION_ENABLED)
        self.shorts_render_button.setEnabled(True)
        self.veo3_auto_button.setEnabled(VEO3_IMAGE_GENERATION_ENABLED)
        if ok:
            self.add_log(f"Tao anh Shorts xong: {message}")
            QMessageBox.information(self, "Shorts anh xong", f"Da tao anh Shorts:\n{message}")
            project_dir = self.selected_project_dir()
            if project_dir:
                open_path(project_dir / "shorts")
            return
        self.add_log(f"Tao anh Shorts loi: {message}")
        QMessageBox.warning(self, "Shorts anh loi", message)

    def start_shorts_render(self) -> None:
        if self.shorts_render_thread and self.shorts_render_thread.isRunning():
            return
        if self.shorts_veo_thread and self.shorts_veo_thread.isRunning():
            QMessageBox.warning(self, "Shorts anh dang chay", "Hay doi anh Shorts tai xong truoc khi edit.")
            return
        project_dir = self.selected_project_dir()
        if not project_dir:
            QMessageBox.warning(self, "Chua chon project", "Hay chon project truoc.")
            return

        shorts_dir = project_dir / "shorts"
        if not shorts_dir.exists():
            QMessageBox.warning(
                self,
                "Chua co Shorts",
                f"Chua co thu muc Shorts trong:\n{shorts_dir}\n\nHay chay workflow tao kich ban truoc.",
            )
            return

        self.settings = load_settings()
        self.shorts_render_button.setEnabled(False)
        self.shorts_veo_button.setEnabled(False)
        self.add_log(f"Bat dau tao voice + edit 2 Shorts cho project: {project_dir.name}")
        self.shorts_render_thread = ShortsRenderThread(self.settings, project_dir, self)
        self.shorts_render_thread.log.connect(self.add_log)
        self.shorts_render_thread.done.connect(self.on_shorts_render_finished)
        self.shorts_render_thread.start()

    def on_shorts_render_finished(self, ok: bool, message: str) -> None:
        self.shorts_render_button.setEnabled(True)
        self.shorts_veo_button.setEnabled(VEO3_IMAGE_GENERATION_ENABLED)
        if ok:
            self.add_log(f"Render Shorts xong:\n{message}")
            QMessageBox.information(self, "Shorts xong", f"Da xuat Shorts:\n{message}")
            project_dir = self.selected_project_dir()
            if project_dir:
                open_path(project_dir / "shorts")
            return
        self.add_log(f"Render Shorts loi: {message}")
        QMessageBox.warning(self, "Shorts loi", message)

    def start_veo3_auto_edit(self) -> None:
        return self.start_veo3_generate()

    def on_veo3_auto_edit_finished(self, ok: bool, message: str) -> None:
        return self.on_veo3_generate_finished(ok, message)

    def pick_thumbnail_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Chọn ảnh thumbnail", "", "Images (*.png *.jpg *.jpeg *.webp);;All files (*.*)")
        if path:
            self.thumbnail_path_input.setText(path)

    def pick_character_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Chọn ảnh nhân vật 9:16", "", "Images (*.png *.webp *.jpg *.jpeg);;All files (*.*)")
        if path:
            self.character_path_input.setText(path)

    def browse_setting_path(self, key: str) -> None:
        field = self.setting_fields[key]
        current = field.text().strip()
        if key in {"projects_dir", "chrome_profile_root", "text_to_voice_root", "veo3_root"}:
            path = QFileDialog.getExistingDirectory(self, "Chọn thư mục", current or str(Path.home()))
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Chọn file", current or str(Path.home()), "All files (*.*)")
        if path:
            field.setText(path)

    def save_settings_from_ui(self) -> None:
        updated = dict(self.settings)
        for key, field in self.setting_fields.items():
            value = field.text().strip()
            if key in {
                "chrome_cdp_port",
                "text_to_voice_port",
                "text_to_voice_max_chars",
                "text_to_voice_timeout",
                "text_to_voice_parallel_jobs",
                "veo3_output_count",
                "veo3_multi_video",
                "veo3_wait_gen_video",
                "veo3_retry_with_error",
                "veo3_wait_resend_video",
                "veo3_token_retry",
                "veo3_token_retry_delay",
                "final_video_width",
                "final_video_height",
                "veo_prompt_limit",
            }:
                try:
                    value = str(int(value))
                except Exception:
                    defaults = {
                        "chrome_cdp_port": 9444,
                        "text_to_voice_port": 7860,
                        "text_to_voice_max_chars": 10000,
                        "text_to_voice_timeout": 1800,
                        "text_to_voice_parallel_jobs": 1,
                        "veo3_output_count": 1,
                        "veo3_multi_video": 3,
                        "veo3_wait_gen_video": 12,
                        "veo3_retry_with_error": 5,
                        "veo3_wait_resend_video": 30,
                        "veo3_token_retry": 5,
                        "veo3_token_retry_delay": 3,
                        "final_video_width": 1920,
                        "final_video_height": 1080,
                        "veo_prompt_limit": 160,
                    }
                    value = str(self.settings.get(key) or defaults[key])
            if key in {"text_to_voice_speed", "final_image_duration"}:
                try:
                    high = 60.0 if key == "final_image_duration" else 2.0
                    low = 4.0 if key == "final_image_duration" else 0.5
                    fallback = 18.0 if key == "final_image_duration" else 1.0
                    value = str(max(low, min(float(value), high)))
                except Exception:
                    value = str(self.settings.get(key) or fallback)
            updated[key] = value
        updated["default_chapter_count"] = 8
        updated["default_target_word_count"] = int(self.settings.get("default_target_word_count") or 6800)
        save_settings(updated)
        self.settings = load_settings()
        self.reload_text_to_voice_controls()
        self.reload_projects()
        QMessageBox.information(self, "Đã lưu", "Đã lưu cấu hình.")

    def closeEvent(self, event) -> None:
        try:
            self.save_current_ui_state()
            if self.pipeline and self.pipeline.isRunning():
                self.pipeline.stop()
        except Exception:
            pass
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
