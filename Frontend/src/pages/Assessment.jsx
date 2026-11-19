import { useMemo, useEffect, useState } from 'react'
import Certificate from './Certificate'
import { api } from '../services/api'
import { triggerCertificateBlast } from '../utils/confettiBlast'
import { getStudentUser } from '../utils/authSession'

const buildDynamicFallbackQuestions = (courseTitle = 'Safety & Operations', lessonTitle = 'Core Principles') => [
  {
    id: 1,
    prompt: `What is the primary operational objective emphasized in "${lessonTitle}"?`,
    options: [
      `Strict adherence to verified operational standards and safety procedures for ${lessonTitle}`,
      'Ignoring pre-shift inspection guidelines',
      'Operating beyond approved manufacturer ratings',
      'Bypassing required personal protective equipment'
    ],
    answer: 0,
    explanation: `Strict adherence to verified operational standards ensures personal safety and procedural compliance for ${lessonTitle}.`
  },
  {
    id: 2,
    prompt: `How are procedures in "${lessonTitle}" validated prior to execution?`,
    options: [
      'By following standard inspection checklists and confirming operational readiness',
      'By guessing operational parameters',
      'By skipping equipment condition checks',
      'By operating damaged machinery without reporting'
    ],
    answer: 0,
    explanation: 'Standard inspection checklists and condition confirmation ensure equipment reliability and prevent operational hazards.'
  },
  {
    id: 3,
    prompt: `What immediate action is required if a non-compliance or hazard is observed in "${lessonTitle}"?`,
    options: [
      'Immediately halt operation, report the hazard, and apply corrective protocols',
      'Continue operation at normal speed',
      'Cover up the hazard without recording it',
      'Wait until the end of the week to mention it'
    ],
    answer: 0,
    explanation: 'Prompt reporting and immediate hazard correction prevent dangerous situations from escalating into severe accidents.'
  },
  {
    id: 4,
    prompt: `Who is responsible for maintaining safety and operational standards during "${lessonTitle}"?`,
    options: [
      'Every team member and operator involved in the workflow',
      'Only external inspectors',
      'Nobody once the shift begins',
      'Only the equipment manufacturer'
    ],
    answer: 0,
    explanation: 'Safety is a shared responsibility across all operators and personnel involved in the workflow.'
  },
  {
    id: 5,
    prompt: `What is the key takeaway from completing the "${lessonTitle}" module in "${courseTitle}"?`,
    options: [
      'Consistently executing operations with discipline, vigilance, and verified compliance',
      'Rushing tasks to finish early without inspections',
      'Treating safety guidelines as optional suggestions',
      'Disabling warning sensors to avoid interruptions'
    ],
    answer: 0,
    explanation: `Consistent discipline, vigilance, and compliance form the cornerstone of operational excellence in "${courseTitle}".`
  }
]

const Assessment = ({ course, lessonTitle, quizQuestions, isFinalAssessment, onExit, onFinishAssessment }) => {
  const [index, setIndex] = useState(0)
  const [selected, setSelected] = useState(null)
  const [isChecked, setIsChecked] = useState(false)
  const [score, setScore] = useState(0)
  const [userAnswers, setUserAnswers] = useState({})
  const [showCertificate, setShowCertificate] = useState(false)
  const [showFailedView, setShowFailedView] = useState(false)

  const user = useMemo(() => getStudentUser() || { fullName: 'Learner', role: 'STUDENT' }, [])

  const userId = useMemo(() => {
    return user?.id || user?.finNumber || user?.email || 'guest';
  }, [user]);

  const activeQuestions = useMemo(() => {
    let source = []
    if (quizQuestions && Array.isArray(quizQuestions) && quizQuestions.length > 0) {
      source = quizQuestions
    } else if (course && course.isCustom && course.quiz && course.quiz.length > 0) {
      source = course.quiz
    } else {
      source = buildDynamicFallbackQuestions(course?.title || 'Safety & Operations', lessonTitle || 'Core Principles')
    }

    const mapped = source.map((q, idx) => {
      const origCorrectIdx = q.answerIndex !== undefined ? q.answerIndex : q.answer !== undefined ? q.answer : 0
      let opts = Array.isArray(q.options) && q.options.length >= 2 ? [...q.options] : ['Option A', 'Option B', 'Option C', 'Option D']

      const correctText = opts[origCorrectIdx] || opts[0]

      // Truly randomize options across positions A, B, C, D using Fisher-Yates shuffle
      for (let i = opts.length - 1; i > 0; i--) {
        const j = Math.floor(Math.random() * (i + 1));
        [opts[i], opts[j]] = [opts[j], opts[i]];
      }
      const finalAnswerIdx = opts.indexOf(correctText)

      let explanationText = q.explanation
      if (!explanationText || explanationText.length < 5) {
        explanationText = correctText
          ? `"${correctText}" is correct because it directly satisfies the requirement described in this question.`
          : 'Review the correct answer highlighted above to strengthen your understanding.'
      }

      return {
        id: idx + 1,
        prompt: q.question || q.prompt || `Question ${idx + 1}`,
        options: opts,
        answer: finalAnswerIdx >= 0 ? finalAnswerIdx : 0,
        explanation: explanationText
      }
    })

    // Guarantee at least 5 questions for single chapter, or at least 10 for final assessment
    const minRequired = isFinalAssessment ? 10 : 5;
    if (mapped.length >= minRequired) {
      return isFinalAssessment ? mapped : mapped.slice(0, 5);
    } else {
      const extraNeeded = minRequired - mapped.length;
      const dynamicList = buildDynamicFallbackQuestions(course?.title || 'Safety & Operations', lessonTitle || 'Core Principles');
      const padding = dynamicList.slice(0, extraNeeded).map((q, idx) => ({
        ...q,
        id: mapped.length + idx + 1
      }));
      return [...mapped, ...padding];
    }
  }, [quizQuestions, course, lessonTitle, isFinalAssessment])

  const question = activeQuestions[index]

  const handleFinish = () => {
    if (onFinishAssessment) {
      onFinishAssessment()
    } else {
      onExit?.()
    }
  }

  const handleActionClick = () => {
    if (!isChecked) {
      // Step 1: Check answer for current question
      if (selected === null) return
      const isCorrect = selected === question.answer
      const updatedScore = score + (isCorrect ? 1 : 0)
      if (isCorrect) {
        setScore(updatedScore)
      }
      setUserAnswers(prev => ({ ...prev, [index]: selected }))
      setIsChecked(true)
    } else {
      // Step 2: Move to next question or evaluate final course completion
      if (index === activeQuestions.length - 1) {
        const isLastLesson = Boolean(
          isFinalAssessment ||
          !course?.lessons ||
          lessonTitle === course?.lessons[course.lessons.length - 1]?.title
        )

        if (isLastLesson) {
          const passThreshold = Math.ceil(activeQuestions.length * 0.6) // 60% passing mark
          if (score >= passThreshold) {
            const completedNow = new Date()
            const formattedNow = completedNow.toLocaleDateString('en-US', {
              month: 'long',
              day: 'numeric',
              year: 'numeric'
            })

            if (course?.id) {
              const allLessonIds = course.lessons ? course.lessons.map(l => String(l.id)) : ['1', '2', '3', '4', '5'];
              try {
                localStorage.setItem(`skillbridge_progress_${userId}_${course.id}`, JSON.stringify(allLessonIds));
                localStorage.setItem(`skillbridge_completed_date_${userId}_${course.id}`, completedNow.toISOString());
              } catch (e) { }
              api.saveCourseProgress(course.id, {
                completed: true,
                progress: 100,
                completedLessonIds: allLessonIds
              }).catch(e => console.warn('Failed to save completion:', e));
            }
            triggerCertificateBlast()
            setShowCertificate(true)
          } else {
            setShowFailedView(true)
          }
        } else {
          handleFinish()
        }
      } else {
        setIndex(prev => prev + 1)
        setSelected(null)
        setIsChecked(false)
      }
    }
  }

  // Calculate percentage score for certificate
  const scorePercentage = useMemo(() => {
    return Math.round((score / Math.max(1, activeQuestions.length)) * 100)
  }, [score, activeQuestions.length])

  // If user completed final assessment and passed, render official Certificate of Completion!
  if (showCertificate) {
    const savedDateStr = course?.id
      ? localStorage.getItem(`skillbridge_completed_date_${userId}_${course.id}`)
      : null
    const certDate = savedDateStr
      ? new Date(savedDateStr).toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' })
      : new Date().toLocaleDateString('en-US', { month: 'long', day: 'numeric', year: 'numeric' })

    return (
      <Certificate
        course={{ ...course, date: certDate }}
        scorePercentage={scorePercentage}
        onBackHome={() => {
          handleFinish()
        }}
      />
    )
  }

  // If user took final assessment but scored below 60%, render Retake prompt
  if (showFailedView) {
    return (
      <div className="min-h-screen w-full bg-[#f4f7fc] flex items-center justify-center p-4">
        <div className="bg-white rounded-3xl border border-slate-200 p-8 max-w-lg w-full text-center shadow-sm">
          <span className="text-4xl">⚠️</span>
          <h2 className="text-xl font-bold text-[#1e2e4a] mt-3">Final Assessment Not Passed</h2>
          <p className="text-xs text-slate-500 font-medium mt-1">
            You scored <span className="font-bold text-amber-600">{scorePercentage}%</span> ({score}/{activeQuestions.length}). You need at least <span className="font-bold text-emerald-600">60%</span> to pass and earn your course certificate.
          </p>
          <div className="flex justify-center gap-3 mt-6">
            <button
              type="button"
              onClick={() => {
                setIndex(0)
                setSelected(null)
                setIsChecked(false)
                setScore(0)
                setUserAnswers({})
                setShowFailedView(false)
              }}
              className="px-6 py-2.5 rounded-xl bg-[#ff7a00] text-white font-bold text-xs border-0 cursor-pointer shadow-xs hover:bg-[#ea6c00]"
            >
              🔄 Retake Assessment
            </button>
            <button
              type="button"
              onClick={handleFinish}
              className="px-6 py-2.5 rounded-xl bg-white border border-slate-300 text-slate-700 font-bold text-xs cursor-pointer hover:bg-slate-50"
            >
              ← Back to Course
            </button>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen w-full overflow-y-auto bg-[#f4f7fc] text-[#0f172a] select-none flex flex-col font-sans px-4 py-3 sm:px-6 sm:py-4">
      {/* Top Left Header with Back Button */}
      <div className="shrink-0 w-full mb-2">
        <button
          type="button"
          onClick={onExit}
          className="inline-flex items-center gap-1.5 text-xs sm:text-sm font-semibold text-[#1e293b] hover:text-[#0f172a] transition border-0 bg-transparent p-0 cursor-pointer"
        >
          <span className="text-base">←</span> Back
        </button>
      </div>

      {/* Main Container */}
      <main className="w-full flex-1 flex justify-center items-start overflow-visible">
        <div className="bg-white rounded-[20px] sm:rounded-[24px] border border-slate-200/80 shadow-xs w-full p-4 sm:p-6 lg:p-8 flex flex-col justify-between min-h-0 overflow-y-auto">

          {/* Header & Title Section */}
          <div className="shrink-0 text-center">
            <h1 className="text-xl sm:text-2xl font-bold text-[#1e2e4a] tracking-tight">
              {isFinalAssessment ? 'Final Course Assessment' : 'Chapter Assessment'}
            </h1>
            <p className="text-xs sm:text-sm text-slate-500 font-medium mt-1">
              {lessonTitle && course?.title && lessonTitle.trim().toLowerCase() !== course.title.trim().toLowerCase()
                ? `${course.title} • ${lessonTitle}`
                : (course?.title || lessonTitle || 'Course Assessment')}
            </p>

            {/* Dynamic Step Progress Bar */}
            <div className="flex items-center gap-1.5 sm:gap-2 max-w-lg mx-auto mt-4 mb-2 w-full">
              {activeQuestions.map((q, i) => {
                let stepColor = 'bg-slate-200/90'
                const isStepChecked = i < index || (i === index && isChecked)

                if (isStepChecked) {
                  stepColor = 'bg-emerald-500'
                } else if (i === index) {
                  stepColor = 'bg-[#ff7a00]'
                }

                return (
                  <div
                    key={q.id}
                    className={`flex-1 h-2 rounded-full transition-all duration-300 ${stepColor}`}
                  />
                )
              })}
            </div>
          </div>

          <div className="flex-1 min-h-0 flex flex-col justify-between my-1 overflow-y-auto pr-0.5">
            {/* Question Header & Prompt */}
            <div className="shrink-0 mt-1">
              <p className="text-xs sm:text-sm text-slate-500 font-semibold">
                Questions {index + 1} of {activeQuestions.length}
              </p>
              <h2 className="text-base sm:text-lg lg:text-xl font-bold text-[#0f172a] mt-1 leading-snug">
                {question?.prompt}
              </h2>
            </div>

            {/* Options List */}
            <div className="flex-1 min-h-0 space-y-2.5 my-2 pr-0.5">
              {question?.options.map((optionText, optionIndex) => {
                const isSelected = selected === optionIndex
                const isCorrectOption = optionIndex === question.answer
                const isUserWrongChoice = isChecked && isSelected && !isCorrectOption

                // Card visual style determination
                let cardStyle = 'bg-white border-slate-200/90 hover:border-slate-300 text-[#0f172a] font-semibold'
                let badgeStyle = 'bg-slate-100 text-slate-800 font-bold'
                let badgeIcon = String.fromCharCode(65 + optionIndex)

                if (isChecked) {
                  if (isCorrectOption) {
                    cardStyle = 'bg-emerald-50/60 border-2 border-emerald-500 text-slate-900 font-medium shadow-2xs'
                    badgeStyle = 'bg-emerald-500 text-white font-bold'
                    badgeIcon = (
                      <svg className="w-4 h-4 fill-none stroke-current stroke-[3]" viewBox="0 0 24 24">
                        <path d="M5 13l4 4L19 7" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                    )
                  } else if (isUserWrongChoice) {
                    cardStyle = 'bg-rose-50/60 border-2 border-rose-500 text-slate-900 font-medium shadow-2xs'
                    badgeStyle = 'bg-rose-500 text-white font-bold'
                    badgeIcon = (
                      <svg className="w-4 h-4 fill-none stroke-current stroke-[3]" viewBox="0 0 24 24">
                        <path d="M6 18L18 6M6 6l12 12" strokeLinecap="round" strokeLinejoin="round" />
                      </svg>
                    )
                  } else {
                    cardStyle = 'bg-white border-slate-200 text-slate-400 opacity-60'
                    badgeStyle = 'bg-slate-100 text-slate-400 font-bold'
                  }
                } else if (isSelected) {
                  cardStyle = 'bg-[#fff8f2] border-2 border-[#ff7a00] text-[#1e2e4a] font-medium shadow-2xs'
                  badgeStyle = 'bg-[#ff7a00] text-white font-bold'
                }

                return (
                  <button
                    key={optionIndex}
                    type="button"
                    disabled={isChecked}
                    onClick={() => !isChecked && setSelected(optionIndex)}
                    className={`w-full flex items-center gap-3.5 px-4 py-3 rounded-2xl border text-left transition cursor-pointer text-xs sm:text-sm ${cardStyle}`}
                  >
                    {/* Left Badge Circle (A, B, C, D or ✓ or ✕) */}
                    <div className={`h-7 w-7 rounded-full flex items-center justify-center shrink-0 transition text-xs ${badgeStyle}`}>
                      {badgeIcon}
                    </div>

                    <span className="leading-relaxed flex-1">{optionText}</span>
                  </button>
                )
              })}

              {/* AI Explanation Feedback Box (Renders when user checks answer) */}
              {isChecked && (
                <div className={`p-4 rounded-2xl text-left border animate-fade-in ${selected === question.answer
                  ? 'bg-emerald-50/90 border-emerald-200/80 text-emerald-900'
                  : 'bg-rose-50/90 border-rose-200/80 text-rose-900'
                  }`}>
                  <div className="flex items-center gap-1.5 text-xs sm:text-sm font-bold">
                    <span className="text-sm">🤖</span>
                    <span className={selected === question.answer ? 'text-emerald-700' : 'text-rose-700'}>
                      {selected === question.answer ? 'Correct! 🎉' : "Not quite. Here's why:"}
                    </span>
                  </div>
                  <p className={`text-xs sm:text-sm font-medium mt-1 leading-relaxed ${selected === question.answer ? 'text-emerald-800' : 'text-rose-800'
                    }`}>
                    {question.explanation}
                  </p>
                </div>
              )}
            </div>

            {/* Bottom Action Button */}
            <div className="shrink-0 text-center pt-5">
              <button
                type="button"
                onClick={handleActionClick}
                disabled={!isChecked && selected === null}
                className="cursor-pointer rounded-2xl bg-[#ff7a00] hover:bg-[#ea6c00] px-12 py-3.5 text-xs sm:text-sm font-bold text-white border-0 transition shadow-sm disabled:opacity-40 disabled:cursor-not-allowed"
              >
                {!isChecked
                  ? 'Check answer'
                  : index === activeQuestions.length - 1
                    ? (isFinalAssessment ? 'Finish Course Assessment >' : 'Next Chapter >')
                    : 'Next Question >'}
              </button>
            </div>
          </div>
        </div>
      </main>
    </div>
  )
}

export default Assessment
