tell application "iTerm2"
  tell current window
      tell current session
          write text "kill $(cat ~/.cedar/pid-frontend-artifacts.txt)"
      end tell
  end tell
end tell
