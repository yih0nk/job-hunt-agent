// One-page, ATS-clean resume in the classic two-row layout (organisation + location,
// role + dates). Data arrives as JSON via sys.inputs, so user text is never parsed as
// Typst markup; the only formatting honoured is **bold**, split out explicitly below.
#let d = json(bytes(sys.inputs.data))
// Vertical spacing multiplier. The app searches for the largest value that still fits one
// page, so whitespace spreads evenly the way LaTeX resumes fill a page.
#let k = d.at("scale", default: 1.0)

#set document(title: d.name + " — Resume", author: d.name)
#set page(paper: "us-letter", margin: (x: 0.5in, top: 0.42in, bottom: 0.4in))
#set text(font: ("New Computer Modern", "Libertinus Serif"), size: 10pt, lang: "en")
#set par(justify: false, leading: 0.42em, spacing: 0.42em)
#set list(indent: 0.7em, body-indent: 0.4em, spacing: 0.28em * k, marker: text(size: 6pt, baseline: -0.6pt)[●])

// "Cut prep from **30 minutes to seconds**" -> plain, bold, plain.
#let rich(s) = {
  let parts = s.split("**")
  for (i, p) in parts.enumerate() {
    if calc.odd(i) and i < parts.len() - 1 { strong(text(p)) } else { text(p) }
  }
}
#let dates(e) = {
  let s = e.at("start", default: "")
  let t = e.at("end", default: "")
  if s != "" and t != "" { s + " – " + t } else if t != "" { t } else { s }
}
#let section(title) = {
  v(0.55em * k)
  block(below: 0.28em, text(size: 11.5pt, smallcaps(title)))
  block(below: 0.4em * k, line(length: 100%, stroke: 0.5pt))
}
#let row(l, r) = grid(columns: (1fr, auto), align: (left, right), column-gutter: 1em, l, r)
// Each bullet reports how many lines it took (queried as <bullet> metadata) so the app can
// enforce one-line bullets by rewording instead of shrinking the font.
#let probe(key, body) = layout(size => {
  let one = measure(block(width: size.width, [Xgjy])).height
  let two = measure(block(width: size.width, [Xgjy \ Xgjy])).height
  let h = measure(block(width: size.width, body)).height
  [#metadata((key: key, lines: 1 + calc.round((h - one) / (two - one)))) <bullet>]
  body
})
// Bullets at 8.5pt, like LaTeX resume templates' \small items: one line holds a full thought.
#let bullets(items, keys: ()) = if items.len() > 0 {
  v(0.05em * k)
  text(size: 8.5pt, list(..items.enumerate().map(((i, b)) => probe(keys.at(i, default: ""), rich(b)))))
}

#align(center)[
  #block(below: 0.45em * k, text(size: 22pt, weight: "bold", d.name))
  #text(size: 9pt, d.contact.filter(x => x != "").enumerate().map(((i, c)) => if i == 0 { text(c) } else { underline(c) }).join("  |  "))
  #if d.at("headline", default: "") != "" [
    #block(above: 0.4em, text(size: 9pt, style: "italic", d.headline))
  ]
]

#if d.education.len() > 0 {
  section("Education")
  for e in d.education {
    row(strong(e.school), e.at("location", default: ""))
    v(-0.12em)
    row(text(size: 9.5pt, emph(e.degree)), text(size: 9.5pt, emph(dates(e))))
    bullets(e.at("details", default: ()))
    v(0.15em * k)
  }
}

#for group in d.sections {
  if group.entries.len() > 0 {
    section(group.title)
    for e in group.entries {
      if e.kind == "project" {
        // Projects: "Name | descriptor | link" on one line, dates right.
        let head = (strong(e.title),)
        if e.at("org", default: "") != "" { head.push(emph(e.org)) }
        if e.at("url", default: "") != "" { head.push(underline(e.url)) }
        row(head.join("  |  "), dates(e))
      } else {
        row(strong(e.org), e.at("location", default: ""))
        v(-0.12em)
        row(text(size: 9.5pt, emph(e.title)), text(size: 9.5pt, emph(dates(e))))
      }
      bullets(e.bullets, keys: e.at("keys", default: ()))
      v(0.18em * k)
    }
  }
}

#if d.skills.len() > 0 {
  section("Technical Skills")
  for g in d.skills {
    if g.items.len() > 0 [
      #text(size: 9.5pt)[#strong(g.name + ": ")#g.items.join(", ")] \
    ]
  }
}
