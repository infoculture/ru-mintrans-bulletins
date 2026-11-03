from mintrans_stats.normalize.tables import column_headers, iter_cells, split_header


def test_split_header_and_multilevel_columns():
    rows = [
        ["", "Перевозка грузов", "", "Грузооборот", ""],
        ["", "2022 г.", "2023 г.", "2022 г.", "2023 г."],
        ["Всего", "116 403,6", "109 321,4", "68 046,8", "56 569,7"],
    ]
    header, data = split_header(rows)
    assert len(header) == 2 and len(data) == 1
    assert column_headers(header, 5) == [
        "",
        "Перевозка грузов / 2022 г.",
        "Перевозка грузов / 2023 г.",
        "Грузооборот / 2022 г.",
        "Грузооборот / 2023 г.",
    ]


def test_iter_cells_skips_empty_and_keeps_notes():
    rows = [
        ["", "2024 г.", "2025 г."],
        ["Транспорт – всего2)", "7 670,1", "7 664,3"],
        ["в том числе:", "", ""],
        ["морской", "...", "4,01)"],
    ]
    cells = list(iter_cells(rows))
    assert [(c.row_label, c.col_header, c.value) for c in cells] == [
        ("Транспорт – всего", "2024 г.", 7670.1),
        ("Транспорт – всего", "2025 г.", 7664.3),
        ("морской", "2024 г.", None),
        ("морской", "2025 г.", 4.0),
    ]
    assert cells[0].row_note == "2)" and cells[-1].value_note == "1)"


def test_numbering_row_is_header_not_data():
    from mintrans_stats.normalize.tables import is_numbering_row

    assert is_numbering_row(["", "1", "", "", "2", "", "3"])
    assert is_numbering_row(["А", "Б", "1", "2", "4", "5"])  # пропуск номера допустим
    assert not is_numbering_row(["Итого", "1", "2", "3"])
    assert not is_numbering_row(["", "1", "1", "2"])
    rows = [
        ["Наименование расходов", "За 2012 год", "За 2013 год", "На 2014 год"],
        ["", "1", "2", "3"],
        ["Всего", "791 917,7", "776 804,1", "836 195,8"],
        ["", "1", "2", "3"],  # шапка, повторённая на новой странице
        ["Международная деятельность", "162,1", "165,3", "161,3"],
    ]
    cells = list(iter_cells(rows))
    assert [(c.row_label, c.col_header) for c in cells][:2] == [
        ("Всего", "За 2012 год"),
        ("Всего", "За 2013 год"),
    ]
    assert len(cells) == 6
