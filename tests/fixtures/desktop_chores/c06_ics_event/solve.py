"""Reference solution for c06_ics_event (scripted, rung: shell). Runs with cwd = scratch."""
lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//AWOS//C10//EN",
         "BEGIN:VEVENT", "UID:c06-dentist@awos.local", "DTSTAMP:20261009T000000Z",
         "SUMMARY:Dentist appointment", "DTSTART:20261103T143000",
         "DTEND:20261103T151500", "LOCATION:Main St Clinic",
         "END:VEVENT", "END:VCALENDAR"]
with open("Calendar/dentist.ics", "w", newline="") as f:
    f.write("\r\n".join(lines) + "\r\n")
