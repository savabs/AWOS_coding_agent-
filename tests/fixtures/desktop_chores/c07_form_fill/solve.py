"""Reference solution for c07_form_fill (scripted, rung: ax). Runs with cwd = scratch."""
p = "form.html"
h = open(p).read()
h = h.replace('name="full_name" type="text" value=""', 'name="full_name" type="text" value="Ada Lovelace"')
h = h.replace('name="email" type="email" value=""', 'name="email" type="email" value="ada@example.org"')
h = h.replace('<option value="free" selected>', '<option value="free">')
h = h.replace('<option value="pro">', '<option value="pro" selected>')
h = h.replace('type="checkbox">', 'type="checkbox" checked>')
h = h.replace('name="notes"></textarea>', 'name="notes">Call after 5pm</textarea>')
open(p, "w").write(h)
