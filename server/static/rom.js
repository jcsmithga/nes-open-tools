// ROM setup: hash each chosen file with SubtleCrypto, compare it with the expected SHA-1,
// and keep verified bytes in the browser's ROM store (romstore.js, loaded first) so players
// choose their ROMs once. The download form on the seed page reads the same store.
//
// A dump that differs only in its iNES header, or has none, is the same game: a file that
// does not match is tried again with the card's data-header in place of its first 16
// bytes when it is data-size long, or prepended when it is one header short, and the
// store keeps the bytes that matched (golf/randomizer/roms.py).
//
// Each card's look is driven by its data-state (server/static/site.css):
//   checking     reading the store or hashing a file
//   empty        nothing stored; the file input is offered
//   error        the chosen file did not match; the file input is offered again
//   stored       a verified ROM is stored; only Forget is offered
//   unavailable  this browser cannot hash or store files
//
// Strings are embedded in #rom-strings; see romstore.js for t().
"use strict";

const t = makeT("rom-strings");

function setState(article, state, html) {
  article.dataset.state = state;
  article.querySelector(".rom-status").innerHTML = html;
}

function hexBytes(hex) {
  return Uint8Array.from(hex.match(/../g), (pair) => parseInt(pair, 16));
}

// The file's bytes with the expected header, or null when its size rules one out.
function withHeader(bytes, header, size) {
  const file = new Uint8Array(bytes);
  if (file.length === size) {
    const fixed = file.slice();
    fixed.set(header);
    return fixed.buffer;
  }
  if (file.length === size - header.length) {
    const fixed = new Uint8Array(size);
    fixed.set(header);
    fixed.set(file, header.length);
    return fixed.buffer;
  }
  return null;
}

function setupRom(article) {
  const id = article.dataset.romId;
  const expected = article.dataset.sha1.toLowerCase();
  const header = hexBytes(article.dataset.header);
  const size = Number(article.dataset.size);
  const input = article.querySelector("input[type=file]");
  const forget = article.querySelector(".rom-forget");

  async function refresh() {
    const record = await getRom(id);
    if (record && record.sha1 === expected) {
      setState(article, "stored", t("rom.status.stored"));
    } else {
      setState(article, "empty", t("rom.status.empty"));
    }
  }

  input.addEventListener("change", async () => {
    const file = input.files[0];
    if (!file) return;
    setState(article, "checking", t("rom.status.checking"));
    try {
      let bytes = await file.arrayBuffer();
      const actual = await sha1Hex(bytes);
      if (actual !== expected) {
        const fixed = withHeader(bytes, header, size);
        if (!fixed || (await sha1Hex(fixed)) !== expected) {
          setState(
            article,
            "error",
            t("rom.status.mismatch", { file: file.name, sha1: actual }),
          );
          return;
        }
        bytes = fixed;
      }
      await putRom({ id, sha1: expected, bytes });
      setState(article, "stored", t("rom.status.stored"));
      forget.focus();
    } catch (error) {
      setState(article, "error", t("rom.status.check_failed", { error }));
    } finally {
      input.value = "";
    }
  });

  forget.addEventListener("click", async () => {
    setState(article, "checking", t("rom.status.forgetting"));
    try {
      await deleteRom(id);
      await refresh();
      input.focus();
    } catch (error) {
      setState(article, "error", t("rom.status.forget_failed", { error }));
    }
  });

  input.disabled = false;
  return refresh();
}

document.addEventListener("DOMContentLoaded", () => {
  const articles = document.querySelectorAll("article.rom");
  if (!window.isSecureContext || !window.crypto?.subtle || !window.indexedDB) {
    document.getElementById("rom-unsupported").hidden = false;
    for (const article of articles) {
      setState(article, "unavailable", t("rom.status.unavailable"));
    }
    return;
  }
  articles.forEach((article) => {
    setupRom(article).catch((error) =>
      setState(article, "error", t("rom.status.storage_failed", { error })),
    );
  });
});
