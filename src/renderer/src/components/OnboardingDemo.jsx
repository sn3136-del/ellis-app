import { useState, useEffect } from 'react'
import { Icon } from './icons.jsx'
import { wordmarkWhite } from '../assets/logos.js'

const SLIDES = [
  {
    type: 'intro',
    title: 'Welcome to Ellis',
    sub: 'The AI workspace that runs immigration operations end to end, for the United States and Canada.'
  },
  {
    type: 'roles',
    title: 'One workspace. Three roles.',
    sub: 'Immigrants, employers, and counsel share the same live case. Ellis tailors the interface to each one.'
  },
  {
    type: 'lifecycle',
    title: 'Immigration is a recurring system.',
    sub: 'Ellis keeps the whole lifecycle moving, not just a single filing.'
  },
  {
    type: 'caps',
    title: 'Ellis does the operational work.',
    sub: 'You bring the documents. Ellis reviews, answers, flags, prepares, and hands off to counsel.'
  },
  {
    type: 'go',
    title: 'Let\u2019s set up your workspace.',
    sub: 'Choose your role to begin. You can switch roles at any time.'
  }
]

const STAGES = ['Onboarding', 'Filing', 'Renewals', 'Travel', 'Compliance', 'Notices / RFEs']
const CAPS = ['Document review', 'Ask Ellis anything', 'Risk flags', 'Notice summaries', 'Form preparation', 'Evidence packets']

export default function OnboardingDemo({ onFinish }) {
  const [i, setI] = useState(0)
  const last = i === SLIDES.length - 1

  useEffect(() => {
    if (last) return
    const t = setTimeout(() => setI((x) => Math.min(x + 1, SLIDES.length - 1)), 4200)
    return () => clearTimeout(t)
  }, [i, last])

  return (
    <div className="demo">
      <div className="demo__bar"><span style={{ width: `${((i + 1) / SLIDES.length) * 100}%` }} /></div>
      <div className="demo__stage">
        {SLIDES.map((s, idx) => (
          <div key={idx} className={'demo__slide' + (idx === i ? ' is-active' : '')}>
            <div className="demo__inner">
              {s.type === 'intro' && <img className="demo__logo" src={wordmarkWhite} alt="Ellis" />}
              <h2 className="demo__title">{s.title}</h2>
              <p className="demo__sub">{s.sub}</p>

              {s.type === 'roles' && (
                <div className="demo__roles">
                  {[['user', 'Immigrant'], ['building', 'Employer'], ['scale', 'Counsel']].map(([ic, label], k) => {
                    const Ico = Icon[ic]
                    return (
                      <div className="demo__rolecard" key={label} style={{ animationDelay: `${k * 0.12}s` }}>
                        <Ico /><h4>{label}</h4>
                      </div>
                    )
                  })}
                </div>
              )}

              {s.type === 'lifecycle' && (
                <div className="demo__lifecycle">
                  {STAGES.map((st, k) => (
                    <span className="demo__stage-pill" key={st} style={{ animationDelay: `${k * 0.1}s` }}>{st}</span>
                  ))}
                </div>
              )}

              {s.type === 'caps' && (
                <div className="demo__caps">
                  {CAPS.map((c, k) => (
                    <div className="demo__cap" key={c} style={{ animationDelay: `${k * 0.08}s` }}>{c}</div>
                  ))}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
      <div className="demo__foot">
        <button className="btn btn--ghost btn--sm" onClick={onFinish}>Skip intro</button>
        <div className="demo__dots">
          {SLIDES.map((_, idx) => <i key={idx} className={idx === i ? 'on' : ''} />)}
        </div>
        {last ? (
          <button className="btn" onClick={onFinish}>Get started <Icon.arrow style={{ width: 16, height: 16 }} /></button>
        ) : (
          <button className="btn" onClick={() => setI((x) => Math.min(x + 1, SLIDES.length - 1))}>Next</button>
        )}
      </div>
    </div>
  )
}
