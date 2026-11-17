// Every page's timestamps in the viewer's own time zone and locale. The server renders each
// as <time datetime="2026-09-23T15:51:09Z">2026-09-23 15:51 UTC</time> (the `timestamp`
// filter, server/views.py); this swaps the text for the local time and keeps the UTC text
// as the hover title. A date alone, <time datetime="2026-10-01">2026-10-01</time> (the
// `calendar_date` filter), is formatted in UTC: it parses as UTC midnight, which the
// viewer's own time zone could show as the day before. A month, <time datetime="2026-11">
// (the `calendar_month` filter), is its month and year, also in UTC. A countdown,
// <time datetime="..." data-countdown> (the `countdown` filter), is how long until then,
// as "in 6 hours", in minutes under two hours, with the local time as its title. Without
// the script the server's text stays, which is still correct.

// A block, so its names stay out of the scope the page's other plain scripts share.
{
  const format = new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
  const formatDate = new Intl.DateTimeFormat(undefined, {
    dateStyle: "medium",
    timeZone: "UTC",
  });
  const formatMonth = new Intl.DateTimeFormat(undefined, {
    year: "numeric",
    month: "long",
    timeZone: "UTC",
  });
  const formatRelative = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
  const MINUTE = 60 * 1000;
  const HOUR = 60 * MINUTE;

  const until = (when) => {
    const left = when.getTime() - Date.now();
    return Math.abs(left) >= 2 * HOUR
      ? formatRelative.format(Math.trunc(left / HOUR), "hour")
      : formatRelative.format(Math.trunc(left / MINUTE), "minute");
  };

  for (const element of document.querySelectorAll("time[datetime]")) {
    const when = new Date(element.dateTime);
    if (Number.isNaN(when.getTime())) {
      continue;
    }
    if (element.hasAttribute("data-countdown")) {
      element.title = format.format(when);
      element.textContent = until(when);
      continue;
    }
    const monthOnly = /^\d{4}-\d{2}$/.test(element.dateTime);
    const dateOnly = !element.dateTime.includes("T");
    element.title = element.textContent;
    element.textContent = (
      monthOnly ? formatMonth : dateOnly ? formatDate : format
    ).format(when);
  }
}
