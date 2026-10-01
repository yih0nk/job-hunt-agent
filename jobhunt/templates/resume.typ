// One-page, ATS-clean resume. Data arrives as JSON via sys.inputs, so no user text is
// ever parsed as Typst markup.
#let d = json(bytes(sys.inputs.data))

#set document(title: d.name + " — Resume", author: d.name)
#set page(paper: "us-letter", margin: (x: 0.55in, y: 0.5in))
#set text(font: ("Libertinus Serif", "New Computer Modern"), size: 10.2pt, lang: "en")
#set par(justify: false, leading: 0.5em, spacing: 0.5em)
#set list(indent: 0.6em, body-indent: 0.45em, spacing: 0.38em, marker: [•])

#let rule() = line(length: 100%, stroke: 0.5pt + luma(90))
#let section(title) = {
  v(0.55em)
  text(size: 10.8pt, weight: "bold", tracking: 0.04em, upper(title))
  v(-0.25em)
  rule()
  v(0.1em)
}
#let dates(e) = {
  let s = e.at("start", default: "")
  let t = e.at("end", default: "")
  if s != "" and t != "" { s + " – " + t } else if t != "" { t } else { s }
}

#align(center)[
  #text(size: 19pt, weight: "bold", d.name) \
  #v(-0.2em)
  #text(size: 9.6pt, d.contact.filter(x => x != "").join("  |  "))
  #if d.at("headline", default: "") != "" [
    \ #text(size: 9.8pt, style: "italic", d.headline)
  ]
]

#if d.education.len() > 0 {
  section("Education")
  for e in d.education {
    grid(columns: (1fr, auto), align: (left, right), row-gutter: 0.35em,
      text(weight: "bold", e.school), text(e.at("location", default: "")),
      text(style: "italic", e.degree + if e.at("gpa", default: "") != "" { ", GPA " + e.gpa } else { "" }),
      text(style: "italic", dates(e)))
    if e.at("details", default: ()).len() > 0 {
      v(0.15em)
      for x in e.details [- #x]
    }
    v(0.25em)
  }
}

#for group in d.sections {
  if group.entries.len() > 0 {
    section(group.title)
    for e in group.entries {
      grid(columns: (1fr, auto), align: (left, right), row-gutter: 0.35em,
        text(weight: "bold", e.title) + if e.at("org", default: "") != "" { text(" — " + e.org) },
        text(dates(e)))
      if e.at("tech", default: ()).len() > 0 {
        v(0.1em)
        text(size: 9.4pt, style: "italic", e.tech.join(", "))
      }
      v(0.15em)
      for b in e.bullets [- #b]
      v(0.3em)
    }
  }
}

#if d.skills.len() > 0 {
  section("Skills")
  for g in d.skills {
    if g.items.len() > 0 [
      #text(weight: "bold", g.name + ": ")#g.items.join(", ") \
    ]
  }
}
