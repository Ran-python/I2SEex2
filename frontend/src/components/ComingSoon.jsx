export default function ComingSoon({ feature }) {
  return (
    <section className="coming-soon card">
      <div className="coming-soon__icon" aria-hidden="true">
        {feature?.icon || '🚧'}
      </div>
      <h1>{feature?.label}</h1>
      <p className="muted">{feature?.description}</p>
      <span className="badge badge--soon">Coming Soon</span>
    </section>
  )
}
