import 'leaflet/dist/leaflet.css'
import L from 'leaflet'
import markerIcon2x from 'leaflet/dist/images/marker-icon-2x.png'
import markerIcon from 'leaflet/dist/images/marker-icon.png'
import markerShadow from 'leaflet/dist/images/marker-shadow.png'
import { useEffect, useMemo, useRef, useState } from 'react'
import { Circle, MapContainer, Marker, TileLayer, useMap, useMapEvents } from 'react-leaflet'

// Fix Leaflet's default marker icon broken-image bug in Vite/Webpack builds
const DEFAULT_MARKER_ICON = L.icon({
  iconUrl: markerIcon,
  iconRetinaUrl: markerIcon2x,
  shadowUrl: markerShadow,
  iconSize: [25, 41],
  iconAnchor: [12, 41],
  popupAnchor: [1, -34],
  tooltipAnchor: [16, -28],
  shadowSize: [41, 41],
})

// Default fallback coordinates (Ariel University campus) if browser geolocation is denied/unavailable
export const DEFAULT_CAMPUS_COORDS = {
  latitude: 32.1042,
  longitude: 35.2078,
}

function MapClickAndPanHandler({ latitude, longitude, onSelect }) {
  const map = useMap()
  const prevRef = useRef({ latitude, longitude })

  useEffect(() => {
    if (
      typeof latitude === 'number' &&
      typeof longitude === 'number' &&
      (prevRef.current.latitude !== latitude || prevRef.current.longitude !== longitude)
    ) {
      prevRef.current = { latitude, longitude }
      map.flyTo([latitude, longitude], Math.max(map.getZoom(), 17), {
        animate: true,
        duration: 0.65,
      })
    }
  }, [latitude, longitude, map])

  useMapEvents({
    click(event) {
      const nextLat = Number(event.latlng.lat.toFixed(6))
      const nextLng = Number(event.latlng.lng.toFixed(6))
      onSelect({ latitude: nextLat, longitude: nextLng })
    },
  })

  return null
}

export default function LocationPickerMap({
  latitude,
  longitude,
  radiusMeters = 50,
  onChange,
}) {
  const [searchQuery, setSearchQuery] = useState('')
  const [searching, setSearching] = useState(false)
  const [searchResults, setSearchResults] = useState([])
  const [statusMessage, setStatusMessage] = useState(null)
  const [locatingGps, setLocatingGps] = useState(false)
  const markerRef = useRef(null)

  const safeLat = typeof latitude === 'number' && !Number.isNaN(latitude) ? latitude : DEFAULT_CAMPUS_COORDS.latitude
  const safeLng = typeof longitude === 'number' && !Number.isNaN(longitude) ? longitude : DEFAULT_CAMPUS_COORDS.longitude
  const safeRadius = Math.max(5, Math.min(5000, Number(radiusMeters) || 50))

  // On initial mount, try to center on the user's current browser geolocation if not yet customized
  useEffect(() => {
    if (!navigator.geolocation) return
    let cancelled = false
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        if (cancelled) return
        onChange({
          latitude: Number(pos.coords.latitude.toFixed(6)),
          longitude: Number(pos.coords.longitude.toFixed(6)),
        })
      },
      () => {
        // Silently keep default coordinates if initial auto-detect is denied or times out
      },
      { enableHighAccuracy: true, timeout: 6000, maximumAge: 30000 },
    )
    return () => {
      cancelled = true
    }
  }, [])

  const handleUseCurrentGps = () => {
    if (!navigator.geolocation) {
      setStatusMessage({
        tone: 'danger',
        text: 'Geolocation is not supported by this browser. Search for an address or click on the map.',
      })
      return
    }
    setLocatingGps(true)
    setStatusMessage(null)
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setLocatingGps(false)
        const nextLat = Number(pos.coords.latitude.toFixed(6))
        const nextLng = Number(pos.coords.longitude.toFixed(6))
        onChange({ latitude: nextLat, longitude: nextLng })
        setStatusMessage({
          tone: 'success',
          text: `📍 Snapped pin to your browser location (${nextLat}, ${nextLng}). Drag the pin or click the map to fine-tune.`,
        })
      },
      (err) => {
        setLocatingGps(false)
        setStatusMessage({
          tone: 'warning',
          text: `Could not read browser GPS (${err.message || 'permission denied'}). Use the address search or click directly on the map.`,
        })
      },
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 0 },
    )
  }

  const handleSearchAddress = async () => {
    const q = searchQuery.trim()
    if (!q) return
    setSearching(true)
    setStatusMessage(null)
    setSearchResults([])
    try {
      const url = `https://nominatim.openstreetmap.org/search?format=json&q=${encodeURIComponent(q)}&limit=5`
      const resp = await fetch(url, {
        headers: {
          Accept: 'application/json',
        },
      })
      if (!resp.ok) {
        throw new Error(`Search service returned ${resp.status}`)
      }
      const list = await resp.json()
      if (!Array.isArray(list) || list.length === 0) {
        setStatusMessage({
          tone: 'warning',
          text: `No locations found for "${q}". Try another place or click directly on the map.`,
        })
        return
      }
      setSearchResults(list)
      // Automatically pan to the top result while also showing the list if there are multiple matches
      const top = list[0]
      const topLat = Number(parseFloat(top.lat).toFixed(6))
      const topLng = Number(parseFloat(top.lon).toFixed(6))
      if (!Number.isNaN(topLat) && !Number.isNaN(topLng)) {
        onChange({ latitude: topLat, longitude: topLng })
      }
    } catch (err) {
      setStatusMessage({
        tone: 'danger',
        text: `Address search failed: ${err.message}`,
      })
    } finally {
      setSearching(false)
    }
  }

  const handleSelectSearchResult = (item) => {
    const nextLat = Number(parseFloat(item.lat).toFixed(6))
    const nextLng = Number(parseFloat(item.lon).toFixed(6))
    if (!Number.isNaN(nextLat) && !Number.isNaN(nextLng)) {
      onChange({ latitude: nextLat, longitude: nextLng })
      setSearchResults([])
      setStatusMessage({
        tone: 'success',
        text: `📍 Selected: ${item.display_name}`,
      })
    }
  }

  const markerEventHandlers = useMemo(
    () => ({
      dragend() {
        const marker = markerRef.current
        if (marker != null) {
          const pos = marker.getLatLng()
          onChange({
            latitude: Number(pos.lat.toFixed(6)),
            longitude: Number(pos.lng.toFixed(6)),
          })
        }
      },
    }),
    [onChange],
  )

  return (
    <div className="location-picker" style={{ width: '100%', textAlign: 'left', marginTop: '0.5rem' }}>
      {/* Address Search & Current GPS Toolbar */}
      <div
        className="context-group__controls"
        style={{ display: 'flex', gap: '0.45rem', flexWrap: 'wrap', marginBottom: '0.5rem' }}
      >
        <input
          type="text"
          placeholder='Search address or campus (e.g. "Ariel University" / "אוניברסיטת אריאל")…'
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              handleSearchAddress()
            }
          }}
          style={{ flex: '1 1 220px' }}
          dir="auto"
          aria-label="Search address or place on map"
        />
        <button
          type="button"
          className="btn btn--secondary btn--sm"
          onClick={handleSearchAddress}
          disabled={searching || !searchQuery.trim()}
        >
          {searching ? '⏳ Searching…' : '🔍 Search'}
        </button>
        <button
          type="button"
          className="btn btn--ghost btn--sm"
          onClick={handleUseCurrentGps}
          disabled={locatingGps}
          title="Center pin on your browser's detected location"
        >
          {locatingGps ? '📍 Locating…' : '🎯 Use My Current GPS Location'}
        </button>
      </div>

      {/* Clickable Search Results Dropdown */}
      {searchResults.length > 0 && (
        <div
          style={{
            marginBottom: '0.55rem',
            padding: '0.45rem',
            borderRadius: '8px',
            border: '1px solid rgba(148, 163, 184, 0.35)',
            background: 'rgba(15, 23, 42, 0.04)',
            maxHeight: '140px',
            overflowY: 'auto',
          }}
        >
          <div style={{ fontSize: '0.78rem', fontWeight: 600, marginBottom: '0.25rem' }}>
            Select a search result ({searchResults.length}):
          </div>
          <div style={{ display: 'grid', gap: '0.25rem' }}>
            {searchResults.map((item) => (
              <button
                key={item.place_id}
                type="button"
                className="btn btn--ghost btn--sm"
                style={{ justifyContent: 'flex-start', textAlign: 'left', fontSize: '0.82rem' }}
                onClick={() => handleSelectSearchResult(item)}
                dir="auto"
              >
                📍 {item.display_name}
              </button>
            ))}
          </div>
        </div>
      )}

      {statusMessage && (
        <div
          className={`notice notice--${statusMessage.tone}`}
          style={{ marginBottom: '0.5rem', padding: '0.45rem 0.65rem', fontSize: '0.83rem' }}
        >
          {statusMessage.text}
        </div>
      )}

      {/* Interactive Leaflet Map with Draggable Pin + Dynamic Radius Circle */}
      <div
        style={{
          height: '300px',
          width: '100%',
          borderRadius: '10px',
          overflow: 'hidden',
          border: '1px solid rgba(148, 163, 184, 0.4)',
          position: 'relative',
          zIndex: 1,
        }}
      >
        <MapContainer
          center={[safeLat, safeLng]}
          zoom={17}
          scrollWheelZoom={true}
          style={{ height: '100%', width: '100%' }}
        >
          <TileLayer
            attribution='&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
            url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
          />
          <MapClickAndPanHandler
            latitude={safeLat}
            longitude={safeLng}
            onSelect={onChange}
          />
          <Marker
            draggable={true}
            eventHandlers={markerEventHandlers}
            position={[safeLat, safeLng]}
            ref={markerRef}
            icon={DEFAULT_MARKER_ICON}
          />
          <Circle
            center={[safeLat, safeLng]}
            radius={safeRadius}
            pathOptions={{
              color: '#2563eb',
              fillColor: '#3b82f6',
              fillOpacity: 0.22,
              weight: 2,
            }}
          />
        </MapContainer>
      </div>

      {/* Live Coordinate & Radius Readout */}
      <div
        style={{
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          flexWrap: 'wrap',
          gap: '0.5rem',
          marginTop: '0.45rem',
          fontSize: '0.84rem',
        }}
      >
        <span>
          📍 <strong>Selected Classroom Pin:</strong>{' '}
          <code>
            {safeLat.toFixed(6)}, {safeLng.toFixed(6)}
          </code>{' '}
          · <strong>Radius:</strong> <code>{Math.round(safeRadius)}m</code>
        </span>
        <span className="muted">Tip: Click anywhere on the map or drag the blue pin to adjust.</span>
      </div>
    </div>
  )
}
