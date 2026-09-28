# Meridian travel images

The app's travel images are stored in the repository, so the trip cards and
recovery views do not depend on an external image service.

- `tuscany-vineyard.jpg`: "Vineyard in Chianti Tuscany" by Jason Parrish,
  [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Vineyard_in_Chianti_Tuscany.jpg),
  licensed under [CC BY 2.0](https://creativecommons.org/licenses/by/2.0/).
- `recovery-flight.jpg`: "ANA 777 landing at NRT" by Angelo DeSantis,
  [Wikimedia Commons](<https://commons.wikimedia.org/wiki/File:ANA_777_landing_at_NRT_(7185678977)_(2).jpg>),
  licensed under [CC BY 2.0](https://creativecommons.org/licenses/by/2.0/).
- `alex-morgan.jpg`, `alsace.jpg`, `coast.jpg`, `douro.jpg`, `mendoza.jpg`,
  `napa.jpg` and `vineyard.jpg`: photographs from Unsplash, used under the
  [Unsplash License](https://unsplash.com/license). They were downloaded from
  the Unsplash URLs the app loaded before July 19, 2026; the photographers are
  not recorded here.
- `haneda-hotel.jpg` (installed August 15, 2026) and every `catalog/*.jpg`
  (all 35 packages, installed September 5, 2026): photorealistic generated
  images supplied by the repository owner and cleared for redistribution in
  this sample. They are not photographs of the places they show and must not
  be described as documentary photography.
- `catalog/CTY-004.jpg` and `catalog/BCH-003.jpg` held earlier catalog
  photography until September 5, 2026, when they were replaced with the rest
  of the catalog. That earlier version is only in git history.
- Each package's image lives at `catalog/<package_id>.jpg`, and
  `scripts/travel_catalog.py` derives `image_url` from the package ID, so a
  replacement at the same path needs no code change.
  `scripts/install_catalog_images.py` crops and resizes new images into place.
- `SHOWCASE_IMAGE_MANIFEST.md` records the intended composition of the catalog
  images the demo prompts return.
