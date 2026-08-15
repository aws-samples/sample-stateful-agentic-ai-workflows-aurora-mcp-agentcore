import { geoEqualEarth, geoGraticule10, geoPath } from 'd3-geo';
import type { FeatureCollection, LineString } from 'geojson';
import { feature } from 'topojson-client';
import type { GeometryCollection, Topology } from 'topojson-specification';
import countries from 'world-atlas/countries-110m.json';

const MAP_WIDTH = 440;
const MAP_HEIGHT = 180;
const JFK: [number, number] = [-73.7781, 40.6413];
const HND: [number, number] = [139.7798, 35.5494];

const topology = countries as unknown as Topology<{
  countries: GeometryCollection;
  land: GeometryCollection;
}>;
const countryFeatures = (
  feature(topology, topology.objects.countries) as FeatureCollection
).features;
const projection = geoEqualEarth()
  .translate([MAP_WIDTH / 2, MAP_HEIGHT / 2])
  .scale(90)
  .rotate([150, 0, 0])
  .center([0, 30]);
const path = geoPath(projection);
const route: LineString = {
  type: 'LineString',
  coordinates: [JFK, HND],
};

function projected(coordinates: [number, number]): [number, number] {
  return projection(coordinates) ?? [0, 0];
}

function AirportMarker({
  code,
  coordinates,
  labelOffset,
}: {
  code: string;
  coordinates: [number, number];
  labelOffset: number;
}) {
  const [x, y] = projected(coordinates);

  return (
    <g transform={`translate(${x} ${y})`}>
      <circle className="mds-route-marker-halo" r={7} />
      <circle className="mds-route-marker" r={3.25} />
      <text
        className="mds-route-label"
        x={labelOffset}
        y={-9}
        textAnchor={labelOffset < 0 ? 'end' : 'start'}
      >
        {code}
      </text>
    </g>
  );
}

export function RecoveryRouteMap() {
  const routePath = path(route) ?? undefined;

  return (
    <div
      className="mds-recovery-route-map"
      role="img"
      aria-label="Pacific-centered map showing the recovery route from John F. Kennedy International Airport to Tokyo Haneda Airport"
    >
      <svg
        className="mds-route-map-svg"
        viewBox={`0 0 ${MAP_WIDTH} ${MAP_HEIGHT}`}
        preserveAspectRatio="xMidYMid meet"
        aria-hidden="true"
      >
        <path className="mds-route-sphere" d={path({ type: 'Sphere' }) ?? undefined} />
        <path className="mds-route-graticule" d={path(geoGraticule10()) ?? undefined} />
        {countryFeatures.map((country, index) => (
          <path
            key={country.id ?? index}
            className="mds-route-geography"
            d={path(country) ?? undefined}
          />
        ))}
        <path className="mds-route-line-glow" d={routePath} />
        <path className="mds-route-line" d={routePath} />
        <AirportMarker code="JFK" coordinates={JFK} labelOffset={-8} />
        <AirportMarker code="HND" coordinates={HND} labelOffset={8} />
      </svg>
      <span className="mds-route-map-caption">JFK · NORTH PACIFIC · HND</span>
    </div>
  );
}
