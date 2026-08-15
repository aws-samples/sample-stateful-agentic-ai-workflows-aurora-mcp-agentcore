# Meridian travel photography

Critical showcase imagery is stored locally so the stage experience does not
depend on third-party image delivery.

- `tuscany-vineyard.jpg`: "Vineyard in Chianti Tuscany" by Jason Parrish,
  [Wikimedia Commons](https://commons.wikimedia.org/wiki/File:Vineyard_in_Chianti_Tuscany.jpg),
  licensed under [CC BY 2.0](https://creativecommons.org/licenses/by/2.0/).
- Other JPEGs were downloaded from the Unsplash photo URLs previously embedded
  in `TripVisual.tsx` and are used under the Unsplash License.
- `catalog/*.jpg` caches the existing Unsplash catalog photography for the
  exact presenter-query results, keeping projected cards reliable if venue
  connectivity is slow.
- `SHOWCASE_IMAGE_MANIFEST.md` documents the preferred real-photo composition
  for each presenter-query asset. A curated replacement can overwrite the
  matching `catalog/<package_id>.jpg` path without a code change.
