from brasileirao_simulator.entrypoints.report.markdown_lite import render


def test_paragraphs_join_lines_and_split_on_blank_lines():
    assert render("uma\nlinha\n\noutra") == "<p>uma linha</p>\n<p>outra</p>"


def test_headings_start_below_the_page_title():
    assert render("# A\n\n## B\n\n### C") == "<h2>A</h2>\n<h3>B</h3>\n<h4>C</h4>"


def test_lists():
    assert render("- um\n- dois") == "<ul><li>um</li><li>dois</li></ul>"


def test_inline_bold_italic_and_links():
    assert render("**forte** e *leve* e [aqui](/analise/)") == \
        '<p><strong>forte</strong> e <em>leve</em> e <a href="/analise/">aqui</a></p>'


def test_html_is_escaped_and_unsafe_links_stay_text():
    assert render("a < b & c") == "<p>a &lt; b &amp; c</p>"
    assert render("[x](javascript:alert(1))") == "<p>[x](javascript:alert(1))</p>"


def test_percent_signs_and_quotes_pass_through():
    assert render('<1% e "aspas"') == '<p>&lt;1% e "aspas"</p>'


def test_a_quote_in_a_link_url_cannot_break_out_of_the_attribute():
    out = render('[a](/x"onerror=alert(1)//)')
    assert "<a " not in out
    assert 'onerror' in out and 'href' not in out
