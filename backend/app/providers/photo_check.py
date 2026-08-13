"""Visa/passport photo compliance — catch the rejection BEFORE submission.

WHAT THIS IS FOR
----------------
A consulate rejects photos for boring, fixable reasons: the file is 3 MB when
the portal accepts 240 kB, the image is 4032x3024 when it must be square, the
background is a beige wall, the applicant is smiling. The applicant finds out
weeks later, at the counter or in a portal error, and loses the appointment.

This module exists so Ellis can tell them BEFORE they submit, while a phone and
a white wall are still an option. It is QUALITY ASSURANCE FOR THE APPLICANT.

WHAT THIS IS NOT
----------------
It is never an attempt to defeat, evade, or reverse-engineer any government or
vendor check. It reports the published requirement a photo does not meet, and
tells the human how to retake it. It has no submit path, it never edits or
"fixes" the image, and it never asserts a photo will be accepted — acceptance
is the consulate's decision, made by a human at a counter, and Ellis does not
speak for them.

WHAT `compliant` MEANS
----------------------
  True       every rule Ellis could actually machine-check passed. It is NOT a
             promise of acceptance, and it is only ever reachable when the
             vendor check ran and returned a verdict for every characteristic.
  False      a published requirement is provably not met (a 300x300 image for a
             600x600 minimum; a characteristic the vendor failed).
  'unknown'  Ellis could not check enough to say. This is the honest answer for
             a photo that passes the deterministic pre-check with no vendor
             configured — the pixels are the right shape and NOTHING is known
             about background, head position, expression, or glasses.

A pass on a partial check is never reported as compliant. That is the whole
point of this file.

THE PARTIAL CHECK (no vendor, no network, no dependency)
--------------------------------------------------------
`precheck_photo` reads the image header out of the bytes — format, width,
height — and compares file size, pixel dimensions and aspect ratio against the
published spec. It runs ALWAYS, including when a vendor is configured, because
those rules are deterministic and a vendor round trip is not needed to know
that a 3 MB photo exceeds a 240 kB limit. Every result it produces is labelled
`checked_by: 'ellis_precheck'` and the payload carries `partial: True` with the
list of rules that were NOT checked.

PRIVACY
-------
The image bytes go to the configured compliance service and nowhere else. Never
to an LLM, never into a log, never into a warning or an error string: a
transport exception can echo a request body, so failures degrade to a short
category instead of the vendor's message.

VENDOR CONTRACT (Regula Face SDK web service)
---------------------------------------------
POST {base}/api/detect
  body: {"tag": ..., "image": "<base64>",
         "processParam": {"onlyCentralFace": true,
                          "quality": {"config": [<characteristic names>]}}}
  response: results.detections[].quality.details[] of
            {name, group, range, result, value}.
  Pass/fail follows Regula's house CheckResult convention, the same one the
  document reader uses for validityStatus: 0 negative, 1 positive, 2 not
  performed. Anything Ellis cannot read as an explicit pass or an explicit fail
  counts as NOT PERFORMED — an unrecognized code can never become a pass.
  source: https://docs.regulaforensics.com/develop/face-sdk/web-service/
          development/usage/face-detection/face-image-quality-check/

There is no default host. The Face SDK is deployed by the operator (their own
container or cloud instance), and Ellis will not invent an endpoint to send an
applicant's face to — a public demo host is not a place for someone's biometric
photograph. Unconfigured means REGULA_BASE_URL is unset, and the partial check
carries the whole result.
"""
from __future__ import annotations

import base64

from ..config import settings

AS_OF = "2026-08-11"

SOURCE_REGULA = "regula"
SOURCE_PRECHECK = "ellis_precheck"

# Refused before anything else: a 25 MB upload is a client bug, not a portrait.
MAX_IMAGE_BYTES = 25 * 1024 * 1024

_DETECT_PATH = "/api/detect"
_TIMEOUT_SECONDS = 30

# Regula CheckResult, the house convention shared with the document reader.
_CHECK_FAIL = 0
_CHECK_PASS = 1


class InvalidPhotoImage(ValueError):
    """The bytes are not a photo. Raised BEFORE any check so an empty upload is
    never assessed and never comes back with a verdict."""


class InvalidPhotoSpec(ValueError):
    """No usable photo spec. Raised rather than defaulting to one government's
    requirements — a spec is a published rule set, and guessing which country's
    rules apply is how a photo gets checked against the wrong ones."""


# ---------------------------------------------------------------------------
# Published specs. Curated data: every entry carries its source and an as_of,
# and a value the source does not publish is None, never a plausible guess.
# ---------------------------------------------------------------------------
_SPEC_DEFAULTS: dict = {
    "name": "", "label": "", "formats": (), "min_width": None,
    "max_width": None, "min_height": None, "max_height": None,
    "aspect_ratio": None, "aspect_tolerance": 0.0, "min_bytes": None,
    "max_bytes": None, "recency_months": None, "source": "", "as_of": AS_OF,
    "notes": (),
    # Which vendor characteristics to ask for. None means the full set. A
    # deployment whose service supports fewer can narrow the ASK here — which
    # is honest — instead of Ellis quietly ignoring the ones that came back
    # unassessed, which would not be.
    "quality_config": None,
}

SPECS: dict[str, dict] = {
    "us_visa_digital": {
        "name": "us_visa_digital",
        "label": "U.S. visa / DS-160 digital photo",
        "formats": ("jpeg",),
        "min_width": 600, "max_width": 1200,
        "min_height": 600, "max_height": 1200,
        # "Height must equal width" — an exact square, so the tolerance is 0.
        "aspect_ratio": 1.0, "aspect_tolerance": 0.0,
        # The published limit is "240 kB". Ellis reads that as 240 * 1024 =
        # 245,760 bytes, the larger of the two readings, so this check can
        # only ever fail a photo that fails under BOTH readings.
        "max_bytes": 240 * 1024,
        "recency_months": 6,
        "source": ("https://travel.state.gov/content/travel/en/us-visas/"
                   "visa-information-resources/photos.html"),
        "as_of": AS_OF,
        "notes": ("square, 600x600 to 1200x1200 pixels, JPEG, 240 kB or less",
                  "plain white or off-white background",
                  "taken within the last 6 months"),
    },
    "uk_digital": {
        "name": "uk_digital",
        "label": "UK digital photo",
        # gov.uk publishes no required file format for a digital photo, so
        # none is enforced here beyond "an image Ellis can read".
        "formats": ("jpeg", "png"),
        "min_width": 600, "min_height": 750,
        "min_bytes": 50 * 1024, "max_bytes": 10 * 1024 * 1024,
        "recency_months": None,
        "source": "https://www.gov.uk/photos-for-passports",
        "as_of": AS_OF,
        "notes": ("at least 600 pixels wide and 750 pixels tall",
                  "50 KB to 10 MB",
                  "plain light-coloured background, plain expression, "
                  "mouth closed"),
    },
    "schengen_visa": {
        "name": "schengen_visa",
        "label": "Schengen visa photo (ICAO 9303 portrait)",
        "formats": ("jpeg", "png"),
        # 35 x 45 mm. The pixel minimum for a DIGITAL submission is set by the
        # receiving consulate or its outsourced centre and is not published
        # centrally, so Ellis does not invent one: those checks report as NOT
        # PERFORMED rather than passing against a made-up number.
        "aspect_ratio": 35 / 45, "aspect_tolerance": 0.02,
        "recency_months": 6,
        "source": ("https://www.icao.int/publications/pages/publication.aspx"
                   "?docnum=9303"),
        "as_of": AS_OF,
        "notes": ("35 x 45 mm, head 70-80% of the frame height",
                  "taken within the last 6 months",
                  "the digital pixel minimum is set by the consulate or its "
                  "visa centre; confirm it on their page"),
    },
    "icao_9303_generic": {
        "name": "icao_9303_generic",
        "label": "ICAO 9303 portrait (generic)",
        "formats": ("jpeg", "png"),
        "recency_months": 6,
        "source": ("https://www.icao.int/publications/pages/publication.aspx"
                   "?docnum=9303"),
        "as_of": AS_OF,
        "notes": ("full face, front view, eyes open, plain background",
                  "no dimension rule is enforced: the receiving authority "
                  "publishes its own"),
    },
}


def spec_for(name: str) -> dict:
    """A published spec by name. An unknown name raises — Ellis will not check
    a photo against a spec it does not have."""
    key = str(name or "").strip().lower()
    if key not in SPECS:
        raise InvalidPhotoSpec(
            f"unknown photo spec '{name}'; known: {', '.join(sorted(SPECS))}")
    return dict(_SPEC_DEFAULTS, **SPECS[key])


def _resolve_spec(spec) -> dict:
    if isinstance(spec, str):
        return spec_for(spec)
    if isinstance(spec, dict) and spec:
        merged = dict(_SPEC_DEFAULTS, **spec)
        if not merged.get("name"):
            merged["name"] = "custom"
        return merged
    raise InvalidPhotoSpec(
        "a photo spec is required: pass a name from SPECS or a spec dict")


# ---------------------------------------------------------------------------
# Image header reading — dimensions without a decoder and without a dependency
# ---------------------------------------------------------------------------
_MAGIC = (
    (b"\xff\xd8\xff", "jpeg"),
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
    (b"BM", "bmp"),
    (b"%PDF", "pdf"),
    (b"II*\x00", "tiff"),
    (b"MM\x00*", "tiff"),
)

# JPEG start-of-frame markers. C4 (Huffman table), C8 (JPEG extension) and CC
# (arithmetic conditioning) share the range but are not frame headers.
_SOF_MARKERS = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}


def _image_format(data: bytes) -> str:
    for magic, name in _MAGIC:
        if data.startswith(magic):
            return name
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in (
            b"heic", b"heix", b"mif1", b"msf1", b"heim"):
        return "heic"
    return ""


def _png_size(data: bytes) -> tuple[int, int] | None:
    if len(data) < 24 or data[12:16] != b"IHDR":
        return None
    return (int.from_bytes(data[16:20], "big"),
            int.from_bytes(data[20:24], "big"))


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    pos, end = 2, len(data)
    while pos + 3 < end:
        if data[pos] != 0xFF:
            pos += 1
            continue
        marker = data[pos + 1]
        if marker in (0xFF, 0x01) or 0xD0 <= marker <= 0xD9:
            pos += 2
            continue
        length = int.from_bytes(data[pos + 2:pos + 4], "big")
        if length < 2:
            return None
        if marker in _SOF_MARKERS:
            if pos + 9 > end:
                return None
            return (int.from_bytes(data[pos + 7:pos + 9], "big"),
                    int.from_bytes(data[pos + 5:pos + 7], "big"))
        pos += 2 + length
    return None


def read_image_header(data: bytes) -> dict:
    """{'format', 'width', 'height'} read straight from the file header.

    No decoder, no dependency, no network. Width/height are None for a format
    whose header Ellis does not parse — unknown stays unknown, and the caller
    reports those checks as not performed rather than passing them.
    """
    fmt = _image_format(bytes(data[:32]))
    size = None
    if fmt == "png":
        size = _png_size(data)
    elif fmt == "jpeg":
        size = _jpeg_size(data)
    return {"format": fmt,
            "width": size[0] if size else None,
            "height": size[1] if size else None}


# ---------------------------------------------------------------------------
# The deterministic pre-check
# ---------------------------------------------------------------------------
def _failure(rule: str, detail: str, how_to_fix: str, checked_by: str) -> dict:
    return {"rule": rule, "detail": detail, "how_to_fix": how_to_fix,
            "checked_by": checked_by}


def _kb(n: int) -> str:
    return f"{n / 1024:.0f} KB"


def precheck_photo(image_bytes: bytes, spec) -> dict:
    """The vendor-free part: format, file size, pixel dimensions, aspect ratio.

    Returns {failures, warnings, checks_performed, checks_not_performed,
    format, width, height}. Deterministic, offline, and explicitly PARTIAL:
    nothing here knows anything about the background, the head, the eyes, or
    the expression.
    """
    resolved = _resolve_spec(spec)
    header = read_image_header(image_bytes)
    fmt, width, height = header["format"], header["width"], header["height"]
    size = len(image_bytes)

    failures: list[dict] = []
    warnings: list[str] = []
    performed: list[str] = []
    not_performed: list[str] = []

    # --- format -----------------------------------------------------------
    allowed = tuple(resolved.get("formats") or ())
    if not fmt:
        failures.append(_failure(
            "file_format",
            "this file is not an image Ellis recognizes",
            "Save the photo as a JPEG and upload that file. A screenshot, a "
            "PDF, or a HEIC straight from an iPhone often will not be "
            "accepted; on an iPhone, Settings > Camera > Formats > Most "
            "Compatible saves JPEGs.",
            SOURCE_PRECHECK))
        performed.append("file_format")
    elif allowed and fmt not in allowed:
        failures.append(_failure(
            "file_format",
            f"the file is {fmt.upper()}; this application accepts "
            f"{', '.join(f.upper() for f in allowed)}",
            f"Re-save the photo as {allowed[0].upper()} and upload that file.",
            SOURCE_PRECHECK))
        performed.append("file_format")
    elif allowed:
        performed.append("file_format")
    else:
        not_performed.append("file_format")
        warnings.append("this spec publishes no required file format")

    # --- file size --------------------------------------------------------
    max_bytes = resolved.get("max_bytes")
    min_bytes = resolved.get("min_bytes")
    if max_bytes or min_bytes:
        performed.append("file_size")
        if max_bytes and size > max_bytes:
            failures.append(_failure(
                "file_size",
                f"the file is {_kb(size)}; the limit is {_kb(max_bytes)}",
                "Re-export the photo at a smaller size or a lower JPEG "
                "quality. Do not crop away the head or shoulders to shrink "
                "it: the framing is part of the requirement.",
                SOURCE_PRECHECK))
        if min_bytes and size < min_bytes:
            failures.append(_failure(
                "file_size",
                f"the file is {_kb(size)}; at least {_kb(min_bytes)} is "
                "required",
                "Upload the original photo rather than one a messaging app "
                "compressed. Re-taking it with the camera app and uploading "
                "that file directly usually fixes this.",
                SOURCE_PRECHECK))
    else:
        not_performed.append("file_size")

    # --- dimensions -------------------------------------------------------
    dim_rules = any(resolved.get(k) for k in
                    ("min_width", "max_width", "min_height", "max_height"))
    has_aspect = resolved.get("aspect_ratio") is not None
    if width is None or height is None:
        if dim_rules:
            not_performed.append("image_dimensions")
        if has_aspect:
            not_performed.append("aspect_ratio")
        warnings.append(
            "Ellis could not read the pixel dimensions from this file, so the "
            "size and shape rules were not checked")
    else:
        if dim_rules:
            performed.append("image_dimensions")
            min_w, max_w = resolved.get("min_width"), resolved.get("max_width")
            min_h, max_h = resolved.get("min_height"), resolved.get("max_height")
            too_small = (min_w and width < min_w) or (min_h and height < min_h)
            too_big = (max_w and width > max_w) or (max_h and height > max_h)
            if too_small:
                bound = f"{min_w or '?'}x{min_h or '?'}"
                failures.append(_failure(
                    "image_dimensions",
                    f"the photo is {width}x{height} pixels; at least {bound} "
                    "is required",
                    "Take a new photo with the camera at full resolution, or "
                    "upload the original instead of a resized copy. Enlarging "
                    "a small photo does not add detail and will still be "
                    "rejected.",
                    SOURCE_PRECHECK))
            if too_big:
                bound = f"{max_w or '?'}x{max_h or '?'}"
                failures.append(_failure(
                    "image_dimensions",
                    f"the photo is {width}x{height} pixels; the maximum is "
                    f"{bound}",
                    f"Resize the photo down to at most {bound} pixels, "
                    "keeping the same framing.",
                    SOURCE_PRECHECK))
        else:
            not_performed.append("image_dimensions")

        if has_aspect:
            performed.append("aspect_ratio")
            target = float(resolved["aspect_ratio"])
            tolerance = float(resolved.get("aspect_tolerance") or 0.0)
            ratio = width / height if height else 0.0
            if abs(ratio - target) > tolerance:
                shape = ("square (width and height equal)" if target == 1.0
                         else f"a width-to-height ratio of about {target:.2f}")
                failures.append(_failure(
                    "aspect_ratio",
                    f"the photo is {width}x{height} (ratio {ratio:.2f}); this "
                    f"application requires {shape}",
                    "Crop the photo to the required shape with the head "
                    "centred and the top of the head near the top edge. Crop "
                    "evenly from the sides rather than cutting off the top of "
                    "the head or the shoulders.",
                    SOURCE_PRECHECK))
        else:
            not_performed.append("aspect_ratio")

    return {"failures": failures, "warnings": warnings,
            "checks_performed": performed,
            "checks_not_performed": not_performed,
            "format": fmt, "width": width, "height": height}


# ---------------------------------------------------------------------------
# The rules only a compliance service can judge, and how a human fixes each
# ---------------------------------------------------------------------------
# Requested from the vendor, and — when no vendor answers — reported verbatim
# as the list of things nobody checked.
_QUALITY_CONFIG = (
    "ImageWidth", "ImageHeight", "ImageWidthToHeight", "PaddingRatio",
    "FaceMidPointHorizontalPosition", "FaceMidPointVerticalPosition",
    "HeadWidthRatio", "HeadHeightRatio", "EyesDistance",
    "Yaw", "Pitch", "Roll", "ShouldersPose",
    "BlurLevel", "NoiseLevel", "FaceDynamicRange", "UnnaturalSkinTone",
    "TooDark", "TooLight", "FaceGlare", "ShadowsOnFace",
    "EyeRightClosed", "EyeLeftClosed", "EyeRightOccluded", "EyeLeftOccluded",
    "EyesRed", "EyeRightCoveredWithHair", "EyeLeftCoveredWithHair", "OffGaze",
    "ExpressionLevel", "MouthOpen", "Smile",
    "DarkGlasses", "ReflectionOnGlasses", "FramesTooHeavy",
    "FaceOccluded", "HeadCovering", "ForeheadCovering", "Headphones",
    "MedicalMask", "StrongMakeup", "ArtFace",
    "BackgroundUniformity", "ShadowsOnBackground", "BackgroundColorMatch",
    "OtherFaces",
)

_RETAKE = ("Retake the photo: stand about two steps in front of a plain, "
           "evenly lit wall, have someone else hold the camera at eye level, "
           "and use the rear camera rather than a selfie.")

# Every entry is (what a human would call it, what the human should DO). A
# failure with no fix is a complaint, not a check.
_FIXES: dict[str, tuple[str, str]] = {
    "ImageWidth": ("image width", "Use a higher-resolution photo: take it "
                   "again with the camera at full quality."),
    "ImageHeight": ("image height", "Use a higher-resolution photo: take it "
                    "again with the camera at full quality."),
    "ImageWidthToHeight": ("image shape", "Crop the photo to the shape this "
                           "application requires, keeping the head centred."),
    "PaddingRatio": ("space around the head", "Re-crop so the head and the "
                     "top of the shoulders fill the frame, with a small even "
                     "margin above the hair."),
    "FaceMidPointHorizontalPosition": (
        "head not centred left-to-right",
        "Re-crop so the centre of the face sits in the middle of the frame."),
    "FaceMidPointVerticalPosition": (
        "head not centred top-to-bottom",
        "Re-crop so the eyes sit a little above the middle of the frame."),
    "HeadWidthRatio": ("head too wide or too narrow in frame",
                       "Move the camera closer or further away and retake, "
                       "rather than zooming in on an existing photo."),
    "HeadHeightRatio": ("head too large or too small in frame",
                        "Retake with the camera about an arm's length away, "
                        "framing from the top of the head to the shoulders."),
    "EyesDistance": ("resolution across the eyes is too low",
                     "Retake closer to the camera at full resolution; a "
                     "cropped-in photo from far away loses this detail."),
    "Yaw": ("head turned to one side", "Face the camera straight on."),
    "Pitch": ("head tilted up or down", "Keep the chin level and look "
              "straight into the lens."),
    "Roll": ("head tilted to one shoulder", "Keep the head upright and "
             "square to the camera."),
    "ShouldersPose": ("shoulders turned", "Square the shoulders to the "
                      "camera rather than standing at an angle."),
    "BlurLevel": ("photo is blurred", "Retake with the camera steady and the "
                  "focus locked on the face; good light helps most."),
    "NoiseLevel": ("photo is grainy", "Retake in brighter light, ideally "
                   "daylight, instead of raising the camera's brightness."),
    "FaceDynamicRange": ("flat or washed-out lighting on the face",
                         "Retake in soft, even light, facing a window."),
    "UnnaturalSkinTone": ("skin tone looks unnatural",
                          "Turn off filters, beauty mode and colour effects, "
                          "and retake in daylight."),
    "TooDark": ("photo is too dark", "Retake facing a window or in brighter "
                "light. Do not brighten the existing file in an editor."),
    "TooLight": ("photo is over-exposed", "Retake out of direct sun or move "
                 "away from the flash."),
    "FaceGlare": ("glare on the face", "Move away from the direct light "
                  "source and retake without flash."),
    "ShadowsOnFace": ("shadows on the face", "Face an even light source with "
                      "nothing casting a shadow across the face."),
    "EyeRightClosed": ("an eye is closed", "Retake with both eyes open and "
                       "clearly visible."),
    "EyeLeftClosed": ("an eye is closed", "Retake with both eyes open and "
                      "clearly visible."),
    "EyeRightOccluded": ("an eye is covered", "Move hair, frames or anything "
                         "else off the eyes and retake."),
    "EyeLeftOccluded": ("an eye is covered", "Move hair, frames or anything "
                        "else off the eyes and retake."),
    "EyesRed": ("red-eye", "Retake without the flash, or with more ambient "
                "light so the flash is not needed."),
    "EyeRightCoveredWithHair": ("hair across an eye",
                                "Move the hair clear of both eyes and retake."),
    "EyeLeftCoveredWithHair": ("hair across an eye",
                               "Move the hair clear of both eyes and retake."),
    "OffGaze": ("not looking at the camera", "Look directly into the lens."),
    "ExpressionLevel": ("expression is not neutral",
                        "Use a neutral expression: mouth closed, no smile."),
    "MouthOpen": ("mouth is open", "Close the mouth and retake."),
    "Smile": ("smiling", "Use a neutral expression with the mouth closed."),
    "DarkGlasses": ("tinted or dark glasses",
                    "Remove sunglasses or tinted lenses and retake."),
    "ReflectionOnGlasses": (
        "reflection on the glasses",
        "Tilt the head very slightly down or move the light, or simply take "
        "the glasses off — most authorities now prefer photos without them."),
    "FramesTooHeavy": ("frames cover the eyes",
                       "Take the glasses off and retake."),
    "FaceOccluded": ("something covers the face",
                     "Remove anything across the face and retake."),
    "HeadCovering": ("head covering",
                     "Head coverings worn daily for religious reasons are "
                     "usually allowed, but the full face from the bottom of "
                     "the chin to the top of the forehead must be visible. "
                     "Check the consulate's own page before submitting."),
    "ForeheadCovering": ("forehead is covered",
                         "Move hair or a head covering clear of the forehead "
                         "and retake."),
    "Headphones": ("headphones or earphones",
                   "Take them off and retake."),
    "MedicalMask": ("face mask", "Remove the mask and retake."),
    "StrongMakeup": ("heavy makeup changes the face",
                     "Retake with everyday makeup so the photo matches the "
                     "face at the counter."),
    "ArtFace": ("this does not look like a live photograph",
                "Submit a real photograph of the person, not a scan of a "
                "printed photo, an avatar or a rendered image."),
    "BackgroundUniformity": ("background is not plain",
                             "Stand in front of a plain wall with nothing "
                             "behind you and retake."),
    "ShadowsOnBackground": ("shadows on the background",
                            "Step further away from the wall, or use softer "
                            "light, so no shadow falls behind you."),
    "BackgroundColorMatch": ("background colour is wrong",
                             "Use a plain white or off-white background "
                             "unless the consulate publishes another colour."),
    "OtherFaces": ("another person is in the photo",
                   "Retake with only the applicant in the frame."),
}

# Recency cannot be established from an image by Ellis or by any vendor: it is
# a fact about when the shutter opened. It is surfaced as a question for the
# human on EVERY result rather than silently assumed.
_RECENCY_QUESTION = (
    "Confirm this photo was taken within the last {months} months. A photo "
    "that no longer looks like the applicant is refused at the counter.")


def _fix_for(name: str) -> tuple[str, str]:
    """(human label, what to do). A characteristic Ellis has no curated fix for
    still gets a real instruction — a failure with no fix is a complaint."""
    return _FIXES.get(name) or (name, _RETAKE)


# ---------------------------------------------------------------------------
# Vendor seam
# ---------------------------------------------------------------------------
def _http_json(method: str, url: str, *, headers: dict,
               json_body: dict | None = None) -> tuple[int, dict]:
    """The single HTTP seam. Every network byte this module sends — including
    the photo — passes through here, so a test that stubs it proves the image
    never left the process."""
    import httpx
    r = httpx.request(method, url, headers=headers, json=json_body,
                      timeout=_TIMEOUT_SECONDS)
    try:
        body = r.json()
    except Exception:  # noqa: BLE001 - a non-JSON body is an error body
        body = {}
    return r.status_code, (body if isinstance(body, dict) else {})


def active_provider() -> str:
    name = (settings().photo_check_provider or "").strip().lower()
    return name if name == SOURCE_REGULA else ""


def is_configured() -> bool:
    """True only when a known provider AND the operator's own service host are
    set. There is no default endpoint: Ellis does not send a face to a host it
    picked. Unconfigured, `check_photo` still runs the deterministic pre-check
    and says plainly that it is partial."""
    return bool(active_provider()) and bool(settings().regula_base_url)


def _detections(body: dict) -> list | None:
    """The detections array, tolerating the documented envelope and a couple of
    flatter shapes. None means the response could not be read at all — which is
    a service problem, NOT a photo with no face in it."""
    if not isinstance(body, dict):
        return None
    candidates = []
    results = body.get("results")
    if isinstance(results, dict):
        candidates.append(results.get("detections"))
        candidates.append(results.get("detection"))
    elif isinstance(results, list):
        candidates.append(results)
    candidates.append(body.get("detections"))
    detection = body.get("detection")
    if isinstance(detection, dict):
        candidates.append(detection.get("faces"))
    for candidate in candidates:
        if isinstance(candidate, list):
            return [d for d in candidate if isinstance(d, dict)]
    return None


def _quality_details(detection: dict) -> list:
    quality = detection.get("quality")
    details = quality.get("details") if isinstance(quality, dict) else None
    return [d for d in details if isinstance(d, dict)] if isinstance(
        details, list) else []


def _verdict(value) -> str:
    """'pass' | 'fail' | 'unknown'. Anything Ellis cannot read as an explicit
    pass or an explicit fail is UNKNOWN — never a pass. An enum this code
    misreads can therefore only ever cost a check, never fake one."""
    if isinstance(value, bool):
        return "pass" if value else "fail"
    if isinstance(value, int):
        if value == _CHECK_PASS:
            return "pass"
        if value == _CHECK_FAIL:
            return "fail"
        return "unknown"
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("1", "true", "ok", "pass", "passed", "positive"):
            return "pass"
        if text in ("0", "false", "fail", "failed", "negative", "error"):
            return "fail"
    return "unknown"


def _range_text(detail: dict) -> str:
    rng = detail.get("range")
    if isinstance(rng, dict):
        low, high = rng.get("min"), rng.get("max")
        if low is not None and high is not None:
            return f" (accepted range {low} to {high})"
    elif isinstance(rng, list) and len(rng) == 2:
        return f" (accepted range {rng[0]} to {rng[1]})"
    return ""


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------
def check_photo(image_bytes: bytes, spec) -> dict:
    """Assess one photo against a published spec, BEFORE it is submitted.

    Returns {available, compliant, failures, warnings, partial,
    checks_performed, checks_not_performed, human_confirmation_required, spec,
    source, note}:

      available   True only when the configured compliance service actually
                  answered. Unconfigured or unreachable it is False, and the
                  deterministic pre-check still carries the result.
      compliant   True | False | 'unknown' — see the module docstring. A pass
                  on a partial check is 'unknown', never True.
      failures    [{rule, detail, how_to_fix, checked_by}]. Every entry names
                  something a human can actually do.
      partial     True whenever any rule went unchecked.
      human_confirmation_required
                  [{rule, question}] — facts no image check can establish, so
                  they are asked instead of assumed.

    Raises InvalidPhotoImage for non-bytes, empty, or absurdly large input and
    InvalidPhotoSpec for a missing or unknown spec — all before any check runs
    and before the provider is asked whether it is configured.
    """
    if not isinstance(image_bytes, (bytes, bytearray)):
        raise InvalidPhotoImage("image_bytes must be bytes")
    if not image_bytes:
        raise InvalidPhotoImage("image_bytes is empty")
    if len(image_bytes) > MAX_IMAGE_BYTES:
        raise InvalidPhotoImage(f"image exceeds {MAX_IMAGE_BYTES} bytes")
    resolved = _resolve_spec(spec)

    pre = precheck_photo(image_bytes, resolved)
    failures = list(pre["failures"])
    warnings = list(pre["warnings"])
    performed = list(pre["checks_performed"])
    not_performed = list(pre["checks_not_performed"])

    human: list[dict] = []
    months = resolved.get("recency_months")
    if months:
        human.append({"rule": "recency",
                      "question": _RECENCY_QUESTION.format(months=months)})

    config = tuple(resolved.get("quality_config") or _QUALITY_CONFIG)
    vendor = _vendor_check(bytes(image_bytes), config) if is_configured() else None
    if vendor is None:
        # Nothing is configured, so everything a service would have judged is
        # unchecked — and the result says exactly which rules those are.
        not_performed.extend(n for n in config if n not in performed)
        note = ("PARTIAL CHECK: no photo-compliance service is configured, so "
                "only file format, size and shape were checked. Background, "
                "head position, expression and eyewear were not checked.")
        compliant = False if failures else "unknown"
        return {"available": False, "compliant": compliant,
                "failures": failures, "warnings": warnings, "partial": True,
                "checks_performed": performed,
                "checks_not_performed": sorted(set(not_performed)),
                "human_confirmation_required": human,
                "spec": resolved.get("name"), "source": SOURCE_PRECHECK,
                "note": note}

    if not vendor["available"]:
        not_performed.extend(n for n in config if n not in performed)
        warnings.extend(vendor["warnings"])
        warnings.append("the photo-compliance service was unavailable")
        compliant = False if failures else "unknown"
        return {"available": False, "compliant": compliant,
                "failures": failures, "warnings": warnings, "partial": True,
                "checks_performed": performed,
                "checks_not_performed": sorted(set(not_performed)),
                "human_confirmation_required": human,
                "spec": resolved.get("name"), "source": SOURCE_PRECHECK,
                "note": ("PARTIAL CHECK: " + vendor["note"] + ", so only file "
                         "format, size and shape were checked.")}

    failures.extend(vendor["failures"])
    warnings.extend(vendor["warnings"])
    performed.extend(vendor["checks_performed"])
    not_performed.extend(vendor["checks_not_performed"])
    not_performed.extend(n for n in config
                         if n not in performed and n not in not_performed)

    partial = bool(not_performed)
    if failures:
        compliant = False
    elif partial:
        # Nothing failed, but something went unchecked. That is not a pass.
        compliant = "unknown"
    else:
        compliant = True

    note = ("no rule Ellis could check failed. This is not a promise of "
            "acceptance: the consulate decides."
            if compliant is True else
            "some rules could not be checked; treat this as incomplete."
            if compliant == "unknown" else
            "this photo does not meet a published requirement; the fixes "
            "below are what a human can do about it.")
    return {"available": True, "compliant": compliant, "failures": failures,
            "warnings": warnings, "partial": partial,
            "checks_performed": sorted(set(performed)),
            "checks_not_performed": sorted(set(not_performed)),
            "human_confirmation_required": human,
            "spec": resolved.get("name"), "source": SOURCE_REGULA,
            "note": note}


def _vendor_check(image_bytes: bytes, config: tuple = _QUALITY_CONFIG) -> dict:
    """One call to the configured compliance service. Returns the vendor's part
    of the answer, or available: False with a short, non-sensitive reason.

    The credential is deliberately not guessed into a header this service does
    not document: a Face SDK deployment sits inside the operator's own
    perimeter behind their own auth. If a deployment needs a credential, it
    belongs here as documented deployment configuration, not as an invented
    header.
    """
    base = (settings().regula_base_url or "").rstrip("/")
    payload = {
        "tag": "ellis-photo-compliance",
        "image": base64.b64encode(image_bytes).decode(),
        "processParam": {"onlyCentralFace": True,
                         "quality": {"config": list(config)}},
    }
    try:
        code, body = _http_json("POST", f"{base}{_DETECT_PATH}",
                                headers={"content-type": "application/json",
                                         "accept": "application/json"},
                                json_body=payload)
    except Exception:  # noqa: BLE001 - never surface transport internals, and
        # never the photo, which an exception string can echo back.
        return {"available": False, "failures": [], "warnings": [],
                "checks_performed": [], "checks_not_performed": [],
                "note": "the photo-compliance service is unreachable"}
    if code >= 400:
        return {"available": False, "failures": [], "warnings": [],
                "checks_performed": [], "checks_not_performed": [],
                "note": f"the photo-compliance service returned HTTP {code}"}

    detections = _detections(body)
    if detections is None:
        return {"available": False, "failures": [], "warnings": [],
                "checks_performed": [], "checks_not_performed": [],
                "note": "the photo-compliance service returned no result"}

    failures: list[dict] = []
    warnings: list[str] = []
    performed: list[str] = []
    not_performed: list[str] = []

    if not detections:
        failures.append(_failure(
            "face_detected", "no face was found in this photo",
            "Upload the applicant's portrait photo. A passport page, a "
            "document scan or a photo taken too far away will not work.",
            SOURCE_REGULA))
        return {"available": True, "failures": failures,
                "warnings": warnings, "checks_performed": ["face_detected"],
                "checks_not_performed": list(config), "note": ""}

    performed.append("face_detected")
    if len(detections) > 1:
        label, fix = _FIXES["OtherFaces"]
        failures.append(_failure("OtherFaces",
                                 f"{label}: {len(detections)} faces were "
                                 "found in this photo", fix, SOURCE_REGULA))

    details = _quality_details(detections[0])
    if not details:
        warnings.append(
            "the photo-compliance service returned no quality assessment")
        not_performed.extend(config)
        return {"available": True, "failures": failures, "warnings": warnings,
                "checks_performed": performed,
                "checks_not_performed": not_performed, "note": ""}

    for detail in details:
        name = str(detail.get("name") or "").strip()
        if not name:
            continue
        verdict = _verdict(detail.get("result", detail.get("status")))
        if verdict == "pass":
            performed.append(name)
            continue
        if verdict == "unknown":
            not_performed.append(name)
            continue
        performed.append(name)
        label, fix = _fix_for(name)
        value = detail.get("value")
        measured = f"; measured {value}" if value is not None else ""
        failures.append(_failure(
            name, f"{label}{_range_text(detail)}{measured}", fix,
            SOURCE_REGULA))

    return {"available": True, "failures": failures, "warnings": warnings,
            "checks_performed": performed,
            "checks_not_performed": not_performed, "note": ""}
